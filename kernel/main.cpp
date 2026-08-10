#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <random>
#include <sstream>
#include <string>
#include "vendor/json.hpp"
#include "audit.hpp"
#include "config.hpp"
#include "ledger.hpp"
#include "rules.hpp"
#include "schema.hpp"
#include "state.hpp"

using json = nlohmann::json;

namespace {

struct PendingHold {
    std::string challenge;
    std::vector<RuleHit> triggered;
};

// 규칙 파일이 바뀌었는지 추적하기 위해 BOOT/CONFIG_RELOAD에 해시를 남긴다.
std::string file_sha256(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in.is_open()) return "";
    std::ostringstream buf;
    buf << in.rdbuf();
    return sha256_hex(buf.str());
}

std::string make_challenge() {
    static std::mt19937_64 rng(std::random_device{}());
    std::ostringstream oss;
    oss << std::hex;
    for (int i = 0; i < 4; i++) oss << rng();
    return oss.str();
}

json triggered_to_json(const std::vector<RuleHit>& triggered) {
    json arr = json::array();
    for (const auto& hit : triggered) {
        arr.push_back({
            {"rule_id", hit.rule_id},
            {"seq", hit.seq},
            {"message_ko", hit.message_ko},
            {"detail", hit.detail},
        });
    }
    return arr;
}

json verdict_to_json(const std::string& request_id, const Verdict& v, const std::string& user_intent = "",
                      const std::string& challenge = "") {
    json out;
    out["type"] = "verdict";
    out["request_id"] = request_id;
    out["user_intent"] = user_intent;
    out["decision"] = decision_to_string(v.decision);
    out["triggered"] = triggered_to_json(v.triggered);
    out["challenge"] = challenge.empty() ? json(nullptr) : json(challenge);
    return out;
}

json deny_r1(const std::string& request_id, const std::string& message_ko, const std::string& detail) {
    Verdict v;
    v.decision = Decision::DENY;
    v.triggered.push_back({Decision::DENY, "R1", 0, message_ko, detail});
    return verdict_to_json(request_id, v);
}

json handle_verify(const json& in, Config& cfg, Ledger& ledger, Audit& audit, const std::string& secret,
                    std::map<std::string, PendingHold>& holds,
                    std::map<std::string, std::map<int, long long>>& pending_amounts,
                    std::map<std::string, Spec>& verified_specs,
                    std::map<std::string, std::string>& baseline_hash) {
    std::string request_id = in.value("request_id", "");
    if (in.contains("spec") && in["spec"].is_object() && in["spec"].contains("request_id")) {
        request_id = in["spec"].value("request_id", request_id);
    }

    SchemaResult sr = parse_and_validate_spec(in);
    if (!sr.ok) {
        // 빈 계획은 AI가 형식을 어긴 게 아니라 "할 수 없다"고 답한 것이다.
        // 판정은 똑같이 DENY지만, 사용자에게 "다시 말해보라"고 오해시키면 안 된다.
        const char* msg = sr.empty_plan
            ? "AI가 이 요청을 수행할 방법을 찾지 못했습니다."
            : "AI가 보낸 지시를 이해할 수 없어 막았습니다.";
        json out = deny_r1(request_id, msg, sr.detail);
        audit.record("VERDICT", out);
        return out;
    }

    AttestationResult ar = parse_attestation(in);
    if (!ar.ok) {
        json out = deny_r1(sr.spec.request_id, "실행 계층의 증언 없이는 검증할 수 없어 막았습니다.", ar.detail);
        audit.record("VERDICT", out);
        return out;
    }

    RuleContext ctx{sr.spec, ar.attestation, cfg, ledger, secret};
    Verdict v = evaluate(ctx);

    if (v.decision != Decision::DENY) {
        // ALLOW/HOLD인 요청만 기억해 둔다 — step_check(TOCTOU)와 commit(장부)에 필요하다.
        verified_specs[sr.spec.request_id] = sr.spec;
        baseline_hash[sr.spec.request_id] = ar.attestation.state_hash;
        for (const auto& st : sr.spec.steps) {
            if ((st.action == "input" || st.action == "select") && cfg.is_amount_field(st.target) && st.has_value) {
                try {
                    pending_amounts[sr.spec.request_id][st.seq] = std::stoll(st.value);
                } catch (...) {}
            }
        }
    }

    json out;
    if (v.decision == Decision::HOLD) {
        std::string challenge = make_challenge();
        holds[sr.spec.request_id] = PendingHold{challenge, v.triggered};
        out = verdict_to_json(sr.spec.request_id, v, sr.spec.user_intent, challenge);
    } else {
        out = verdict_to_json(sr.spec.request_id, v, sr.spec.user_intent);
    }
    audit.record("VERDICT", out);
    return out;
}

json handle_step_check(const json& in, std::map<std::string, Spec>& verified_specs,
                        std::map<std::string, std::string>& baseline_hash, Audit& audit) {
    std::string request_id = in.value("request_id", "");
    int seq = in.value("seq", 0);

    json out;
    out["type"] = "step_check";
    out["request_id"] = request_id;
    out["seq"] = seq;

    AttestationResult ar = parse_attestation(in);
    auto sit = verified_specs.find(request_id);
    auto hit = baseline_hash.find(request_id);
    if (!ar.ok || sit == verified_specs.end() || hit == baseline_hash.end()) {
        out["decision"] = "DENY";
        out["message_ko"] = "검증되지 않은 요청이라 막았습니다.";
        audit.record("STEP_CHECK", out);
        return out;
    }

    const SpecStep* step = nullptr;
    for (const auto& st : sit->second.steps) {
        if (st.seq == seq) { step = &st; break; }
    }

    const std::string& new_hash = ar.attestation.state_hash;
    if (new_hash == hit->second) {
        out["decision"] = "ALLOW";
    } else if (step && step->action == "navigate" && ar.attestation.state_view.page == step->target) {
        // 의도된 navigate로 인한 상태 변화 -> 새 해시를 다음 스텝의 기준값으로 갱신한다.
        hit->second = new_hash;
        out["decision"] = "ALLOW";
    } else {
        out["decision"] = "DENY";
        out["message_ko"] = "실행 도중 화면이 바뀌어 막았습니다.";
    }

    audit.record("STEP_CHECK", out);
    return out;
}

json handle_resolve_hold(const json& in, std::map<std::string, PendingHold>& holds, Audit& audit) {
    std::string request_id = in.value("request_id", "");
    std::string challenge = in.value("challenge", "");
    std::string decision_str = in.value("decision", "");

    json out;
    out["type"] = "resolve_hold";
    out["request_id"] = request_id;

    auto it = holds.find(request_id);
    if (it == holds.end() || challenge.empty() || it->second.challenge != challenge) {
        out["decision"] = "DENY";
        out["message_ko"] = "확인 정보가 올바르지 않아 거부했습니다.";
        audit.record("HOLD_RESOLVED", out);
        return out;
    }

    bool approved = (decision_str == "approve");
    out["decision"] = approved ? "ALLOW" : "DENY";
    holds.erase(it);
    audit.record("HOLD_RESOLVED", out);
    return out;
}

json handle_commit(const json& in, Ledger& ledger, Audit& audit,
                    std::map<std::string, std::map<int, long long>>& pending_amounts,
                    std::map<std::string, Spec>& verified_specs,
                    std::map<std::string, std::string>& baseline_hash) {
    std::string request_id = in.value("request_id", "");
    int seq = in.value("seq", 0);
    json result = in.value("result", json::object());

    auto rit = pending_amounts.find(request_id);
    if (rit != pending_amounts.end()) {
        auto sit = rit->second.find(seq);
        if (sit != rit->second.end()) {
            ledger.add(sit->second);
            rit->second.erase(sit);
        }
    }

    // navigate 스텝이 실제로 의도한 목적지로 이동했다면, 다음 step_check의 기준 해시를 갱신한다.
    // step_check는 그 navigate를 실행하기 "직전"에 이미 통과했으므로, 기준값 갱신은
    // 여기(실행 직후 commit 시점)에서 해야 다음 스텝의 step_check가 정확히 비교할 수 있다.
    if (in.contains("attestation")) {
        AttestationResult ar = parse_attestation(in);
        auto sspec = verified_specs.find(request_id);
        if (ar.ok && sspec != verified_specs.end()) {
            const SpecStep* step = nullptr;
            for (const auto& st : sspec->second.steps) {
                if (st.seq == seq) { step = &st; break; }
            }
            if (step && step->action == "navigate" && ar.attestation.state_view.page == step->target) {
                baseline_hash[request_id] = ar.attestation.state_hash;
            }
        }
    }

    json payload;
    payload["request_id"] = request_id;
    payload["seq"] = seq;
    payload["result"] = result;
    if (in.contains("snapshot")) {
        // 스냅샷 파일 자체는 orchestrator가 관리하지만, 경로+해시를 해시체인에 새겨두면
        // 스냅샷 파일이 사후에 변조됐는지도 감사 로그와 대조해 탐지할 수 있다.
        payload["snapshot"] = in["snapshot"];
    }
    audit.record("EXECUTED", payload);

    return {{"type", "commit_ack"}, {"request_id", request_id}, {"seq", seq}};
}

json handle_undo(const json& in, Audit& audit) {
    std::string request_id = in.value("request_id", "");
    json payload;
    payload["request_id"] = request_id;
    payload["snapshot_path"] = in.value("snapshot_path", "");
    payload["snapshot_hash"] = in.value("snapshot_hash", "");
    audit.record("UNDO", payload);
    return {{"type", "undo_ack"}, {"request_id", request_id}};
}

} // namespace

int main(int argc, char** argv) {
    std::ios::sync_with_stdio(false);

    std::string log_path = (argc > 1) ? argv[1] : "../logs/audit.jsonl";
    std::string config_path = (argc > 2) ? argv[2] : "../config/rules.json";
    std::string secret = (argc > 3) ? argv[3] : "";

    Audit audit(log_path);
    Config cfg = Config::load(config_path);
    Ledger ledger;
    std::map<std::string, PendingHold> holds;
    std::map<std::string, std::map<int, long long>> pending_amounts;
    std::map<std::string, Spec> verified_specs;
    std::map<std::string, std::string> baseline_hash;

    audit.record("BOOT", {{"config_version", cfg.config_version}, {"config_hash", file_sha256(config_path)}});

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;
        auto t0 = std::chrono::steady_clock::now();
        json out;
        try {
            json in = json::parse(line);
            std::string type = in.value("type", "");
            if (type == "ping") {
                out = {{"type", "pong"}};
            } else if (type == "verify") {
                out = handle_verify(in, cfg, ledger, audit, secret, holds, pending_amounts, verified_specs, baseline_hash);
            } else if (type == "step_check") {
                out = handle_step_check(in, verified_specs, baseline_hash, audit);
            } else if (type == "resolve_hold") {
                out = handle_resolve_hold(in, holds, audit);
            } else if (type == "commit") {
                out = handle_commit(in, ledger, audit, pending_amounts, verified_specs, baseline_hash);
            } else if (type == "undo") {
                out = handle_undo(in, audit);
            } else if (type == "reload_config") {
                cfg.reload();
                audit.record("CONFIG_RELOAD", {{"config_version", cfg.config_version}, {"config_hash", file_sha256(cfg.path)}});
                out = {{"type", "ok"}};
            } else if (type == "audit_verify") {
                out = audit.verify_chain();
                out["type"] = "audit_verify";
            } else {
                out = {{"type", "error"}, {"message", "unknown or not-yet-implemented type: " + type}};
            }
        } catch (const std::exception& e) {
            // 파싱조차 실패 = R1 위반. 절대 통과시키지 않는다.
            out = deny_r1("", "AI가 보낸 지시를 이해할 수 없어 막았습니다.", e.what());
            audit.record("VERDICT", out);
        }
        auto t1 = std::chrono::steady_clock::now();
        out["elapsed_us"] = std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();

        std::cout << out.dump(-1, ' ', /*ensure_ascii=*/true) << "\n";
        std::cout.flush(); // 빼먹으면 Python이 영원히 멈춘다.
    }
    return 0;
}
