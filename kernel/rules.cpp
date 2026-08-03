#include "rules.hpp"
#include <algorithm>
#include <chrono>
#include <ctime>
#include "audit.hpp"

const char* decision_to_string(Decision d) {
    switch (d) {
        case Decision::ALLOW: return "ALLOW";
        case Decision::HOLD:  return "HOLD";
        case Decision::DENY:  return "DENY";
    }
    return "DENY";
}

namespace {

bool try_parse_amount(const std::string& value, long long& out) {
    if (value.empty()) return false;
    try {
        size_t pos = 0;
        long long v = std::stoll(value, &pos);
        if (pos != value.size() || v < 0) return false;
        out = v;
        return true;
    } catch (...) {
        return false;
    }
}

bool is_amount_step(const SpecStep& st, const Config& cfg) {
    return (st.action == "input" || st.action == "select") && cfg.is_amount_field(st.target);
}

} // namespace

// R1 — 스키마 검증.
// JSON 파싱 실패 및 구조적 위반(필수 필드·action 5종·seq 연속)은
// schema.cpp::parse_and_validate_spec()에서 이미 걸러진 뒤에만 여기 도달한다.
// 남은 R1 검사(target이 state_view.form_fields/허용 URL에 있는지)는 Phase 5에서 채워진다.
std::vector<RuleHit> rule_schema(const RuleContext& ctx) {
    (void)ctx;
    return {};
}

// R5 — 상태·근거 대조. 이 프로젝트의 심장.
// R5-a: AI가 주장한 화면 상태 != 실행 계층이 증언한 실제 상태 -> DENY (전체 중단)
//       + attestation.hmac을 커널이 직접 재계산해 검증 -> AI는 증언을 위조할 수 없다.
// R5-b: 금액·수취인처럼 사람이 다치는 값은 반드시 evidence 경로로 근거를 대야 한다.
//       근거가 없거나, 경로가 존재하지 않거나, 값이 다르면 DENY.
//       숨겨진 배너로 지어낸 계좌·금액은 state_view 어디에도 없으므로 근거를 댈 수 없다.
std::vector<RuleHit> rule_state_grounding(const RuleContext& ctx) {
    std::vector<RuleHit> hits;

    if (!ctx.spec.claimed_state.present || ctx.spec.claimed_state.state_hash.empty() ||
        ctx.spec.claimed_state.state_hash != ctx.att.state_hash) {
        hits.push_back({Decision::DENY, "R5", 0,
            "AI가 본 화면과 실제 화면이 달라서 막았습니다.",
            "claimed_state.state_hash != attestation.state_hash"});
        return hits; // 상태 자체가 안 맞으면 evidence 검사는 의미가 없다
    }

    std::string expected_hmac = hmac_sha256_hex(ctx.hmac_secret, canonical_dump(ctx.att.state_view.raw));
    if (expected_hmac != ctx.att.hmac) {
        hits.push_back({Decision::DENY, "R5", 0,
            "실행 계층의 증언을 신뢰할 수 없어 막았습니다.",
            "attestation.hmac이 재계산 값과 일치하지 않습니다"});
        return hits;
    }

    for (const auto& st : ctx.spec.steps) {
        bool needs_grounding = (st.action == "input" || st.action == "select") &&
                                (ctx.cfg.is_amount_field(st.target) || ctx.cfg.is_payee_field(st.target));
        if (!needs_grounding) continue;

        if (!st.has_evidence || st.evidence.empty()) {
            hits.push_back({Decision::DENY, "R5", st.seq,
                "근거 없는 값이라 막았습니다.",
                "target='" + st.target + "'에 evidence가 없습니다"});
            continue;
        }
        EvidenceResult ev = resolve_evidence(ctx.att.state_view.raw, st.evidence);
        if (!ev.found) {
            hits.push_back({Decision::DENY, "R5", st.seq,
                "근거가 실제 화면에 없어 막았습니다.",
                "evidence='" + st.evidence + "' 경로를 찾을 수 없습니다"});
            continue;
        }
        if (ev.value_str != st.value) {
            hits.push_back({Decision::DENY, "R5", st.seq,
                "근거와 실제 값이 달라서 막았습니다.",
                "evidence 값 '" + ev.value_str + "' != 주장한 값 '" + st.value + "'"});
        }
    }

    return hits;
}

// R2 — 비가역 행동 본인확인. irreversible 플래그 또는 cfg.irreversible_targets에 해당하면 HOLD.
std::vector<RuleHit> rule_irreversible(const RuleContext& ctx) {
    std::vector<RuleHit> hits;
    for (const auto& st : ctx.spec.steps) {
        if (st.action != "click") continue;
        if (st.irreversible || ctx.cfg.is_irreversible_target(st.target)) {
            hits.push_back({Decision::HOLD, "R2", st.seq,
                "되돌릴 수 없는 행동입니다. 본인 확인이 필요합니다.",
                "target='" + st.target + "'"});
        }
    }
    return hits;
}

// R3 — 금액 한도. 1회 상한, 일일 누적 한도, 잔액 초과를 모두 검사한다.
std::vector<RuleHit> rule_amount_limit(const RuleContext& ctx) {
    std::vector<RuleHit> hits;
    for (const auto& st : ctx.spec.steps) {
        if (!is_amount_step(st, ctx.cfg)) continue;

        long long amount = 0;
        if (!st.has_value || !try_parse_amount(st.value, amount)) {
            hits.push_back({Decision::DENY, "R3", st.seq,
                "금액이 올바르지 않아 막았습니다.", "value='" + st.value + "'"});
            continue;
        }
        if (amount > ctx.cfg.per_tx_limit) {
            hits.push_back({Decision::DENY, "R3", st.seq,
                "1회 한도를 넘는 금액이라 막았습니다.",
                std::to_string(amount) + " > per_tx_limit(" + std::to_string(ctx.cfg.per_tx_limit) + ")"});
            continue;
        }
        if (ctx.ledger.daily_total() + amount > ctx.cfg.daily_limit) {
            hits.push_back({Decision::DENY, "R3", st.seq,
                "오늘 이체 한도를 넘어서 막았습니다.",
                "daily_total(" + std::to_string(ctx.ledger.daily_total()) + ") + " + std::to_string(amount) +
                " > daily_limit(" + std::to_string(ctx.cfg.daily_limit) + ")"});
            continue;
        }
        if (amount > ctx.att.state_view.balance) {
            hits.push_back({Decision::DENY, "R3", st.seq,
                "잔액이 부족해 막았습니다.",
                std::to_string(amount) + " > balance(" + std::to_string(ctx.att.state_view.balance) + ")"});
        }
    }
    return hits;
}

// R4 — 미등록 수취인. 등록 목록과 정확히 일치해야 한다 (유사 문자열 허용 금지).
std::vector<RuleHit> rule_unknown_payee(const RuleContext& ctx) {
    std::vector<RuleHit> hits;
    const auto& allowed = ctx.att.state_view.registered_payees;
    for (const auto& st : ctx.spec.steps) {
        if (st.action != "select" && st.action != "input") continue;
        if (!ctx.cfg.is_payee_field(st.target)) continue;

        bool found = std::find(allowed.begin(), allowed.end(), st.value) != allowed.end();
        if (!found) {
            hits.push_back({Decision::DENY, "R4", st.seq,
                "처음 보는 곳으로 보내려 했습니다.",
                "수취인 '" + st.value + "'이(가) 등록 목록에 없습니다."});
        }
    }
    return hits;
}

// R6 — 행동 예산. 스텝 수, navigate 횟수, 입력 길이를 제한한다.
std::vector<RuleHit> rule_budget(const RuleContext& ctx) {
    std::vector<RuleHit> hits;
    if ((int)ctx.spec.steps.size() > ctx.cfg.max_steps) {
        hits.push_back({Decision::DENY, "R6", 0,
            "한 번에 너무 많은 일을 시키려 해서 막았습니다.",
            std::to_string(ctx.spec.steps.size()) + " > max_steps(" + std::to_string(ctx.cfg.max_steps) + ")"});
    }
    int nav_count = 0;
    for (const auto& st : ctx.spec.steps) {
        if (st.action == "navigate") nav_count++;
        if (st.has_value && (int)st.value.size() > ctx.cfg.max_input_chars) {
            hits.push_back({Decision::DENY, "R6", st.seq,
                "입력값이 너무 길어서 막았습니다.",
                "len=" + std::to_string(st.value.size()) + " > max_input_chars(" + std::to_string(ctx.cfg.max_input_chars) + ")"});
        }
    }
    if (nav_count > ctx.cfg.max_navigations) {
        hits.push_back({Decision::DENY, "R6", 0,
            "페이지를 너무 많이 옮겨 다니려 해서 막았습니다.",
            std::to_string(nav_count) + " > max_navigations(" + std::to_string(ctx.cfg.max_navigations) + ")"});
    }
    return hits;
}

// R7 — 모델 신뢰도. 임계값 미만이면 HOLD (필드 자체가 없으면 R1에서 이미 DENY).
std::vector<RuleHit> rule_confidence(const RuleContext& ctx) {
    if (ctx.spec.model_confidence < ctx.cfg.confidence_threshold) {
        return {{Decision::HOLD, "R7", 0,
            "AI가 스스로도 확신하지 못하는 판단입니다. 본인 확인이 필요합니다.",
            "confidence=" + std::to_string(ctx.spec.model_confidence) +
            " < threshold(" + std::to_string(ctx.cfg.confidence_threshold) + ")"}};
    }
    return {};
}

// R8 — 동의·약관 조작 금지. AI는 절대 동의를 대신할 수 없다 (HOLD 아님, 항상 DENY).
std::vector<RuleHit> rule_consent_block(const RuleContext& ctx) {
    std::vector<RuleHit> hits;
    for (const auto& st : ctx.spec.steps) {
        if (ctx.cfg.is_consent_target(st.target)) {
            hits.push_back({Decision::DENY, "R8", st.seq,
                "약관 동의는 AI가 대신할 수 없어 막았습니다.",
                "target='" + st.target + "'"});
        }
    }
    return hits;
}

// R9 — 심야 시간대(기본 00~06시) 금전 이동은 HOLD.
std::vector<RuleHit> rule_night_transfer(const RuleContext& ctx) {
    bool has_money_step = false;
    for (const auto& st : ctx.spec.steps) {
        if (is_amount_step(st, ctx.cfg)) { has_money_step = true; break; }
    }
    if (!has_money_step) return {};

    std::time_t t = std::time(nullptr);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &t);
#else
    gmtime_r(&t, &tm_utc);
#endif
    int hour = tm_utc.tm_hour;
    bool is_night = (ctx.cfg.night_start_hour <= ctx.cfg.night_end_hour)
        ? (hour >= ctx.cfg.night_start_hour && hour < ctx.cfg.night_end_hour)
        : (hour >= ctx.cfg.night_start_hour || hour < ctx.cfg.night_end_hour);

    if (is_night) {
        return {{Decision::HOLD, "R9", 0,
            "심야 시간대의 금전 이동입니다. 본인 확인이 필요합니다.",
            "hour=" + std::to_string(hour)}};
    }
    return {};
}

// rules.cpp의 이 표가 곧 "사람이 읽을 수 있는 안전 명세"다.
// 순서: 구조적 위반(R1) -> 근본 방어(R5) -> 예산(R6) -> 절대 금지(R8) -> 수취인(R4) -> 금액(R3)
//       -> 비가역(R2, HOLD) -> 신뢰도(R7, HOLD) -> 심야(R9, HOLD)
const std::vector<RuleEntry> ALL_RULES = {
    {"R1", "스키마 검증", rule_schema},
    {"R5", "상태·근거 대조", rule_state_grounding},
    {"R6", "행동 예산", rule_budget},
    {"R8", "동의·약관 조작 금지", rule_consent_block},
    {"R4", "미등록 수취인", rule_unknown_payee},
    {"R3", "금액 한도", rule_amount_limit},
    {"R2", "비가역 행동 본인확인", rule_irreversible},
    {"R7", "모델 신뢰도", rule_confidence},
    {"R9", "심야 금전 이동", rule_night_transfer},
};

Verdict evaluate(const RuleContext& ctx) {
    // 모든 규칙을 끝까지 평가해 위반을 전부 모은다 ("부분 실행 금지"는 실행을 막는다는
    // 뜻이지, 첫 DENY에서 나머지 규칙 평가 자체를 생략하라는 뜻이 아니다).
    // 예: 인젝션 공격은 보통 R4(미등록 수취인)와 R5(근거 없음)를 동시에 위반한다 —
    // 둘 다 보여줘야 "AI가 왜 막혔는지"를 온전히 설명할 수 있다.
    // 최종 판정 우선순위는 DENY > HOLD > ALLOW이며, 어떤 순서로 평가되든 DENY가 한 번
    // 정해지면 이후의 HOLD로 격하되지 않는다.
    Verdict v;
    v.decision = Decision::ALLOW;
    for (const auto& r : ALL_RULES) {
        for (const auto& hit : r.fn(ctx)) {
            v.triggered.push_back(hit);
            if (hit.decision == Decision::DENY) {
                v.decision = Decision::DENY;
            } else if (hit.decision == Decision::HOLD && v.decision != Decision::DENY) {
                v.decision = Decision::HOLD;
            }
        }
    }
    return v;
}
