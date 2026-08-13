#include "config.hpp"
#include <algorithm>
#include <fstream>
#include <stdexcept>
#include "vendor/json.hpp"

using json = nlohmann::json;

namespace {

/**
 * JSON 배열을 std::vector<std::string>으로 변환
 * 문자열이 아닌 요소는 조용히 무시(스킵)한다.
 */
std::vector<std::string> to_str_vec(const json& j) {
    std::vector<std::string> out;
    if (j.is_array()) {
        for (const auto& e : j) {
            if (e.is_string()) out.push_back(e.get<std::string>());
        }
    }
    return out;
}

/**
 * 문자열 목록(v) 안에 특정 문자열(s)이 포함되어 있는지 확인
 * is_payee_field 등 아래 조회 함수들이 전부 이걸 감싸서 구현됨
 */
bool contains(const std::vector<std::string>& v, const std::string& s) {
    return std::find(v.begin(), v.end(), s) != v.end();
}

} /* namespace */

Config Config::load(const std::string& rules_path) {
    std::ifstream in(rules_path);
    if (!in.is_open()) {
        /**
         * 손상되거나 없는 설정으로 진행하지 않는다 ? fail-safe.
         * 설정 없이 커널이 돌아가면 한도가 무한대로 풀리는 등 더 위험하므로
         * 아예 예외를 던져서 커널 자체가 시작되지 못하게 막는다.
         */
        throw std::runtime_error("설정 파일을 열 수 없습니다: " + rules_path);
    }
    json j = json::parse(in); /* 손상된 JSON이면 예외 -> 커널 시작 자체가 실패해야 한다 */

    Config cfg;
    cfg.path = rules_path; /* reload() 에서 같은 경로로 다시 읽기 위해 저장 */

    /**
     * 아래 value(key, default)들은 "파일에 키가 없으면 이 기본값을 쓴다"는 뜻.
     * 즉 파일 자체는 반드시 있어야 하지만(위에서 이미 검사)
     * 그 안의 개별 항목이 빠진 건 기본값으로 관대하게 처리한다.
     */
    cfg.config_version = j.value("config_version", "1.0");
    cfg.per_tx_limit = j.value("per_tx_limit", (long long)100000);
    cfg.daily_limit = j.value("daily_limit", (long long)300000);
    cfg.confidence_threshold = j.value("confidence_threshold", 0.6);
    cfg.max_steps = j.value("max_steps", 8);
    cfg.max_navigations = j.value("max_navigations", 3);
    cfg.max_input_chars = j.value("max_input_chars", 200);

    /**
     * night_hours는 [시작시각, 끝시각] 형태의 2개짜리 배열이어야만 유효하게 읽는다
     * (형태가 안 맞으면 그냥 건너뛰고 헤더의 기본값을 그대로 둔다)
     */
    if (j.contains("night_hours") && j["night_hours"].is_array() && j["night_hours"].size() == 2) {
        cfg.night_start_hour = j["night_hours"][0].get<int>();
        cfg.night_end_hour = j["night_hours"][1].get<int>();
    }
    cfg.timezone_offset_hours = j.value("timezone_offset_hours", 0);

    /* 아래 5개는 rules.cpp의 각 규칙이 참조하는 "필드/URL 분류 목록" */
    cfg.payee_fields = to_str_vec(j.value("payee_fields", json::array()));
    cfg.amount_fields = to_str_vec(j.value("amount_fields", json::array()));
    cfg.irreversible_targets = to_str_vec(j.value("irreversible_targets", json::array()));
    cfg.consent_targets = to_str_vec(j.value("consent_targets", json::array()));
    cfg.allowed_urls = to_str_vec(j.value("allowed_urls", json::array()));
    return cfg;
}

/**
 * 설정 파일을 같은 경로에서 다시 읽어 현재 객체를 통째로 교체
 * main.cpp의 "reload_config" 명령이 들어오면 호출됨 ? 재시작 없이 설정 갱신 가능
 */
void Config::reload() {
    *this = Config::load(path);
}

/**
 * 아래는 전부 contains() 한 줄로 감싼 조회 함수들.
 * rules.cpp에서 ctx.cfg.is_amount_field(st.target) 같은 형태로 호출된다.
 */
bool Config::is_payee_field(const std::string& target) const { return contains(payee_fields, target); }
bool Config::is_amount_field(const std::string& target) const { return contains(amount_fields, target); }
bool Config::is_irreversible_target(const std::string& target) const { return contains(irreversible_targets, target); }
bool Config::is_consent_target(const std::string& target) const { return contains(consent_targets, target); }
bool Config::is_allowed_url(const std::string& url) const { return contains(allowed_urls, url); }
