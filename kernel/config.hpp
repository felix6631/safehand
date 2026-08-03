#pragma once
#include <string>
#include <vector>

// rules.json 로딩. 임계값은 코드가 아니라 여기 있는 파일에 있다 —
// reload()로 재빌드 없이 즉석 반영할 수 있다.

struct Config {
    std::string config_version;
    long long per_tx_limit = 100000;
    long long daily_limit = 300000;
    double confidence_threshold = 0.6;
    int max_steps = 8;
    int max_navigations = 3;
    int max_input_chars = 200;
    int night_start_hour = 0;
    int night_end_hour = 6;
    std::vector<std::string> payee_fields;
    std::vector<std::string> amount_fields;
    std::vector<std::string> irreversible_targets;
    std::vector<std::string> consent_targets;
    std::vector<std::string> allowed_urls;

    std::string path;

    static Config load(const std::string& rules_path);
    void reload();

    bool is_payee_field(const std::string& target) const;
    bool is_amount_field(const std::string& target) const;
    bool is_irreversible_target(const std::string& target) const;
    bool is_consent_target(const std::string& target) const;
    bool is_allowed_url(const std::string& url) const;
};
