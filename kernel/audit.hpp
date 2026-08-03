#pragma once
#include <string>
#include "vendor/json.hpp"

// 해시 체인 감사 로그. logs/audit.jsonl 에 한 줄씩 append-only로 기록한다.
// 레코드 하나라도 손대면 그 뒤 전체 체인의 재계산이 어긋난다 (audit_verify로 탐지).

class Audit {
public:
    explicit Audit(const std::string& path);

    // 이벤트를 기록하고 기록된 레코드의 seq를 반환한다.
    long long record(const std::string& event, const nlohmann::json& payload);

    nlohmann::json verify_chain() const;

private:
    std::string path_;
    std::string prev_hash_;
    long long next_seq_ = 1;

    void load_tail();
};

std::string canonical_dump(const nlohmann::json& j);
std::string sha256_hex(const std::string& data);
