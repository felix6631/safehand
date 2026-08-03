#include "config.hpp"
#include <algorithm>
#include <fstream>
#include <stdexcept>
#include "vendor/json.hpp"

using json = nlohmann::json;

namespace {

std::vector<std::string> to_str_vec(const json& j) {
    std::vector<std::string> out;
    if (j.is_array()) {
        for (const auto& e : j) {
            if (e.is_string()) out.push_back(e.get<std::string>());
        }
    }
    return out;
}

bool contains(const std::vector<std::string>& v, const std::string& s) {
    return std::find(v.begin(), v.end(), s) != v.end();
}

} // namespace

Config Config::load(const std::string& rules_path) {
    std::ifstream in(rules_path);
    if (!in.is_open()) {
        // 손상되거나 없는 설정으로 진행하지 않는다 — fail-safe.
        throw std::runtime_error("설정 파일을 열 수 없습니다: " + rules_path);
    }
    json j = json::parse(in); // 손상된 JSON이면 예외 -> 커널 시작 자체가 실패해야 한다

    Config cfg;
    cfg.path = rules_path;
    cfg.config_version = j.value("config_version", "1.0");
    cfg.per_tx_limit = j.value("per_tx_limit", (long long)100000);
    cfg.daily_limit = j.value("daily_limit", (long long)300000);
    cfg.confidence_threshold = j.value("confidence_threshold", 0.6);
    cfg.max_steps = j.value("max_steps", 8);
    cfg.max_navigations = j.value("max_navigations", 3);
    cfg.max_input_chars = j.value("max_input_chars", 200);
    if (j.contains("night_hours") && j["night_hours"].is_array() && j["night_hours"].size() == 2) {
        cfg.night_start_hour = j["night_hours"][0].get<int>();
        cfg.night_end_hour = j["night_hours"][1].get<int>();
    }
    cfg.payee_fields = to_str_vec(j.value("payee_fields", json::array()));
    cfg.amount_fields = to_str_vec(j.value("amount_fields", json::array()));
    cfg.irreversible_targets = to_str_vec(j.value("irreversible_targets", json::array()));
    cfg.consent_targets = to_str_vec(j.value("consent_targets", json::array()));
    cfg.allowed_urls = to_str_vec(j.value("allowed_urls", json::array()));
    return cfg;
}

void Config::reload() {
    *this = Config::load(path);
}

bool Config::is_payee_field(const std::string& target) const { return contains(payee_fields, target); }
bool Config::is_amount_field(const std::string& target) const { return contains(amount_fields, target); }
bool Config::is_irreversible_target(const std::string& target) const { return contains(irreversible_targets, target); }
bool Config::is_consent_target(const std::string& target) const { return contains(consent_targets, target); }
bool Config::is_allowed_url(const std::string& url) const { return contains(allowed_urls, url); }
