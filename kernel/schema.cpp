#include "schema.hpp"
#include <set>

using json = nlohmann::json;

namespace {

const std::set<std::string> ALLOWED_ACTIONS = {"navigate", "input", "select", "click", "read"};

bool get_string(const json& j, const char* key, std::string& out) {
    if (!j.contains(key) || !j[key].is_string()) return false;
    out = j[key].get<std::string>();
    return true;
}

} // namespace

SchemaResult parse_and_validate_spec(const json& in) {
    SchemaResult result;

    if (!in.contains("spec") || !in["spec"].is_object()) {
        result.ok = false;
        result.detail = "spec 필드가 없거나 객체가 아닙니다";
        return result;
    }
    const json& j = in["spec"];
    Spec spec;

    if (!get_string(j, "schema_version", spec.schema_version) || spec.schema_version != "1.0") {
        result.ok = false;
        result.detail = "schema_version이 없거나 지원하지 않는 버전입니다";
        return result;
    }
    if (!get_string(j, "request_id", spec.request_id) || spec.request_id.empty()) {
        result.ok = false;
        result.detail = "request_id가 없습니다";
        return result;
    }
    get_string(j, "user_intent", spec.user_intent); // 선택 필드

    if (!j.contains("model_confidence") || !j["model_confidence"].is_number()) {
        result.ok = false;
        result.detail = "model_confidence가 없거나 숫자가 아닙니다";
        return result;
    }
    spec.model_confidence = j["model_confidence"].get<double>();
    spec.has_confidence = true;

    if (j.contains("claimed_state") && j["claimed_state"].is_object()) {
        const json& cs = j["claimed_state"];
        get_string(cs, "page", spec.claimed_state.page);
        if (cs.contains("balance") && cs["balance"].is_number_integer())
            spec.claimed_state.balance = cs["balance"].get<long long>();
        get_string(cs, "state_hash", spec.claimed_state.state_hash);
        spec.claimed_state.present = true;
    } else {
        result.ok = false;
        result.detail = "claimed_state가 없습니다";
        return result;
    }

    if (!j.contains("steps") || !j["steps"].is_array() || j["steps"].empty()) {
        result.ok = false;
        result.empty_plan = j.contains("steps") && j["steps"].is_array(); // 배열이되 비어 있음
        result.detail = result.empty_plan ? "steps가 비어 있습니다 (AI가 제안한 행동 없음)"
                                          : "steps가 없거나 배열이 아닙니다";
        return result;
    }

    int expected_seq = 1;
    for (const auto& sj : j["steps"]) {
        if (!sj.is_object()) {
            result.ok = false;
            result.detail = "steps 원소가 객체가 아닙니다";
            return result;
        }
        SpecStep step;
        if (!sj.contains("seq") || !sj["seq"].is_number_integer()) {
            result.ok = false;
            result.detail = "step.seq가 없거나 정수가 아닙니다";
            return result;
        }
        step.seq = sj["seq"].get<int>();
        if (step.seq != expected_seq) {
            result.ok = false;
            result.detail = "step.seq가 1부터 연속하지 않습니다 (기대값 " +
                             std::to_string(expected_seq) + ", 실제 " + std::to_string(step.seq) + ")";
            return result;
        }
        expected_seq++;

        if (!get_string(sj, "action", step.action) || !ALLOWED_ACTIONS.count(step.action)) {
            result.ok = false;
            result.detail = "허용되지 않은 action입니다: " +
                             (sj.contains("action") ? sj["action"].dump() : std::string("(없음)"));
            return result;
        }
        if (!get_string(sj, "target", step.target) || step.target.empty()) {
            result.ok = false;
            result.detail = "step.target이 없습니다";
            return result;
        }
        if (sj.contains("value")) {
            if (sj["value"].is_string()) {
                step.value = sj["value"].get<std::string>();
            } else if (sj["value"].is_number() || sj["value"].is_boolean()) {
                step.value = sj["value"].dump();
            } else {
                result.ok = false;
                result.detail = "step.value 형식이 올바르지 않습니다";
                return result;
            }
            step.has_value = true;
        }
        if (sj.contains("irreversible") && sj["irreversible"].is_boolean()) {
            step.irreversible = sj["irreversible"].get<bool>();
        }
        if (sj.contains("evidence") && sj["evidence"].is_string()) {
            step.evidence = sj["evidence"].get<std::string>();
            step.has_evidence = true;
        }

        spec.steps.push_back(step);
    }

    result.ok = true;
    result.spec = spec;
    return result;
}
