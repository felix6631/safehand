#include "schema.hpp"
#include <set>

using json = nlohmann::json;

namespace {

// AI가 시킬 수 있는 행동은 이 5가지뿐. 목록에 없는 action은 무조건 거부됨
const std::set<std::string> ALLOWED_ACTIONS = {"navigate", "input", "select", "click", "read"};

// j[key]가 존재하고 문자열 타입일 때만 out에 꺼내 담고 true 반환.
// 이 파일 전체에서 반복되는 "필드 있는지 + 타입 맞는지" 체크를 한 곳에 모은 헬퍼
bool get_string(const json& j, const char* key, std::string& out) {
    if (!j.contains(key) || !j[key].is_string()) return false;
    out = j[key].get<std::string>();
    return true;
}

} // namespace


// AI가 보낸 요청(spec)이 형식적으로 올바른지 검증하고, 통과하면 Spec 구조체로 변환한다.
// 여기서 하는 건 "형식이 맞는가"뿐이며, "내용이 안전한가"는 다루지 않는다(그건 rules.cpp의 몫).
// main.cpp에서 가장 먼저 호출되는 검증 단계(R1)이며, 실패 시 뒤의 어떤 규칙도 평가하지 않는다.
SchemaResult parse_and_validate_spec(const json& in) {
    SchemaResult result;
    
    // 요청의 몸통인 "spec" 객체가 있어야 함
    if (!in.contains("spec") || !in["spec"].is_object()) {
        result.ok = false;
        result.detail = "spec 필드가 없거나 객체가 아닙니다";
        return result;
    }
    const json& j = in["spec"];
    Spec spec;

    // 스키마 버전이 정확히 "1.0"이어야 함
    // 나중에 형식이 바뀌어도 예전 버전 요청을 잘못 해석하지 않도록 막는 안전장치
    if (!get_string(j, "schema_version", spec.schema_version) || spec.schema_version != "1.0") {
        result.ok = false;
        result.detail = "schema_version이 없거나 지원하지 않는 버전입니다";
        return result;
    }

    // 요청 고유 ID. 없으면 이후 step_check/commit에서 이 요청을 추적할 수 없으므로 필수
    if (!get_string(j, "request_id", spec.request_id) || spec.request_id.empty()) {
        result.ok = false;
        result.detail = "request_id가 없습니다";
        return result;
    }
    get_string(j, "user_intent", spec.user_intent); // 선택 필드

    // AI 스스로 매긴 확신도. 필수 필드이며, rules.cpp의 R7(모델 신뢰도)에서 사용됨
    if (!j.contains("model_confidence") || !j["model_confidence"].is_number()) {
        result.ok = false;
        result.detail = "model_confidence가 없거나 숫자가 아닙니다";
        return result;
    }
    spec.model_confidence = j["model_confidence"].get<double>();
    spec.has_confidence = true;

    // claimed_state: "AI가 스스로 주장하는 화면 상태" ? 실행 계층의 진짜 증언(attestation)과는
    // 별개다. 여기선 형식(페이지/잔액/해시가 있는지)만 확인하고, 진짜인지 검증은
    // rules.cpp R5에서 attestation과 대조해서 이루어진다.
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

    // AI가 하려는 행동들의 목록. 최소 1개는 있어야 함
    if (!j.contains("steps") || !j["steps"].is_array() || j["steps"].empty()) {
        result.ok = false;
        result.empty_plan = j.contains("steps") && j["steps"].is_array(); // 배열이되 비어 있음
        result.detail = result.empty_plan ? "steps가 비어 있습니다 (AI가 제안한 행동 없음)"
                                          : "steps가 없거나 배열이 아닙니다";
        return result;
    }

    int expected_seq = 1; // seq는 1부터 빠짐없이 연속해야 함(순서 조작/생략 방지)
    for (const auto& sj : j["steps"]) {
        if (!sj.is_object()) {
            result.ok = false;
            result.detail = "steps 원소가 객체가 아닙니다";
            return result;
        }
        SpecStep step;

        // 각 스텝의 seq가 정수이고, 1,2,3...처럼 정확히 이어지는지 확인
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

        // action은 반드시 ALLOWED_ACTIONS 5종 중 하나여야 함
        if (!get_string(sj, "action", step.action) || !ALLOWED_ACTIONS.count(step.action)) {
            result.ok = false;
            result.detail = "허용되지 않은 action입니다: " +
                             (sj.contains("action") ? sj["action"].dump() : std::string("(없음)"));
            return result;
        }
        // target(어디에 이 행동을 할지, 예: 버튼/필드 이름)도 필수
        if (!get_string(sj, "target", step.target) || step.target.empty()) {
            result.ok = false;
            result.detail = "step.target이 없습니다";
            return result;
        }
        // value(입력할 값)는 선택 필드. 문자열/숫자/불린만 허용하고, 그 외 타입(배열/객체)이면 거부
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
        // irreversible: 되돌릴 수 없는 행동인지 AI가 스스로 표시하는 플래그(rules.cpp R2에서 사용)
        if (sj.contains("irreversible") && sj["irreversible"].is_boolean()) {
            step.irreversible = sj["irreversible"].get<bool>();
        }
        // evidence: 이 값을 화면 어디서 봤는지 나타내는 경로(rules.cpp R5의 resolve_evidence가 사용)
        if (sj.contains("evidence") && sj["evidence"].is_string()) {
            step.evidence = sj["evidence"].get<std::string>();
            step.has_evidence = true;
        }

        spec.steps.push_back(step);
    }

    // 모든 검사를 통과했으면 성공 반환
    result.ok = true;
    result.spec = spec;
    return result;
}
