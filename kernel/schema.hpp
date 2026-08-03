#pragma once
#include <string>
#include <vector>
#include "vendor/json.hpp"

// 행동 명세(Spec) — 지능 계층(AI)이 만드는, 신뢰하지 않는 입력.
// 여기서 만드는 구조체는 R1(스키마 검증)을 통과한 뒤에만 채워진다.

struct SpecStep {
    int seq = 0;
    std::string action;   // navigate | input | select | click | read
    std::string target;
    std::string value;
    bool has_value = false;
    bool irreversible = false;
    std::string evidence;
    bool has_evidence = false;
};

struct ClaimedState {
    std::string page;
    long long balance = 0;
    std::string state_hash;
    bool present = false;
};

struct Spec {
    std::string schema_version;
    std::string request_id;
    std::string user_intent;
    double model_confidence = -1.0;
    bool has_confidence = false;
    ClaimedState claimed_state;
    std::vector<SpecStep> steps;
};

// R1 스키마 검증 결과. ok == false 면 detail에 실패 사유가 담긴다.
struct SchemaResult {
    bool ok = true;
    std::string detail;
    Spec spec;
};

SchemaResult parse_and_validate_spec(const nlohmann::json& in);
