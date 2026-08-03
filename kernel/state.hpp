#pragma once
#include <string>
#include <vector>
#include "vendor/json.hpp"

// attestation — 실행 계층(executor)이 만드는, 신뢰하는 입력.
// AI(planner)는 이 값을 만들 수 없다 (I3 격리 규칙).
//
// Phase 4에서는 구조화된 필드(balance, registered_payees 등)만 뽑아 쓴다.
// evidence 경로 조회·HMAC 서명 검증은 Phase 5에서 state.cpp에 추가된다.

struct AttestedStateView {
    std::string page;
    long long balance = 0;
    std::vector<std::string> registered_payees;
    std::vector<std::string> form_fields;
    nlohmann::json raw; // evidence 경로 조회(Phase 5)를 위해 원본 그대로 보관
};

struct Attestation {
    bool present = false;
    std::string att_version;
    std::string state_hash;
    std::string hmac;
    AttestedStateView state_view;
};

struct AttestationResult {
    bool ok = true;
    std::string detail;
    Attestation attestation;
};

AttestationResult parse_attestation(const nlohmann::json& in);

// HMAC-SHA256 (RFC 2104). attestation.hmac 검증에 쓰인다 — AI는 이 값을 위조할 수 없다
// (secret은 커널과 executor만 알고, planner 프로세스의 환경에는 없다).
std::string hmac_sha256_hex(const std::string& key, const std::string& msg);

// evidence 경로 조회 결과. found==false면 경로가 없거나 형식이 잘못된 것이다.
struct EvidenceResult {
    bool found = false;
    std::string value_str; // 원본 타입과 무관하게 비교 가능한 문자열로 변환된 값
};

// 지원 문법: key / key.sub / key[3] / key[0].sub (그 이상은 미지원 — 파서가 커지면 취약점이 된다)
EvidenceResult resolve_evidence(const nlohmann::json& root, const std::string& path);
