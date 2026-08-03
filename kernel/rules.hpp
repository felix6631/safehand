#pragma once
#include <string>
#include <vector>
#include "config.hpp"
#include "ledger.hpp"
#include "schema.hpp"
#include "state.hpp"

// R1~R9 규칙 엔진. 규칙 추가 = 함수 하나 + ALL_RULES 배열 한 줄.
// 이 파일 + rules.cpp가 "사람이 전부 읽을 수 있는 안전 명세"다 (500줄 이내 목표).

enum class Decision { ALLOW, HOLD, DENY };

struct RuleHit {
    Decision decision;
    std::string rule_id;
    int seq = 0;              // 몇 번째 스텝에서 걸렸나 (전체 규칙이면 0)
    std::string message_ko;   // 사용자에게 보여줄 쉬운 말
    std::string detail;       // 로그용 상세
};

struct RuleContext {
    const Spec& spec;             // AI의 주장 (신뢰 X)
    const Attestation& att;       // 시스템의 사실 (신뢰 O)
    const Config& cfg;            // rules.json
    const Ledger& ledger;         // 오늘 누적 이체액 등
    const std::string& hmac_secret; // attestation 위조 검증용 (R5)
};

struct Verdict {
    Decision decision = Decision::ALLOW;
    std::vector<RuleHit> triggered;
};

using RuleFn = std::vector<RuleHit> (*)(const RuleContext&);

struct RuleEntry {
    const char* id;
    const char* title;
    RuleFn fn;
};

extern const std::vector<RuleEntry> ALL_RULES;

std::vector<RuleHit> rule_schema(const RuleContext& ctx);
std::vector<RuleHit> rule_state_grounding(const RuleContext& ctx);        // R5
std::vector<RuleHit> rule_irreversible(const RuleContext& ctx);           // R2
std::vector<RuleHit> rule_amount_limit(const RuleContext& ctx);           // R3
std::vector<RuleHit> rule_unknown_payee(const RuleContext& ctx);          // R4
std::vector<RuleHit> rule_budget(const RuleContext& ctx);                 // R6
std::vector<RuleHit> rule_confidence(const RuleContext& ctx);             // R7
std::vector<RuleHit> rule_consent_block(const RuleContext& ctx);          // R8
std::vector<RuleHit> rule_night_transfer(const RuleContext& ctx);         // R9

// 평가 순서 원칙: DENY가 하나라도 나오면 즉시 중단(부분 실행 금지).
// DENY 없고 HOLD 있으면 최종 HOLD. 전부 통과하면 ALLOW.
Verdict evaluate(const RuleContext& ctx);

const char* decision_to_string(Decision d);
