#include "audit.hpp"
#include <chrono>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <sstream>
#include "vendor/picosha2.h"

using json = nlohmann::json;

namespace {

std::string genesis_hash() { return std::string(64, '0'); }

std::string now_iso8601() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    std::time_t t = system_clock::to_time_t(now);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &t);
#else
    gmtime_r(&t, &tm_utc);
#endif
    std::ostringstream oss;
    oss << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%S");
    oss << '.' << std::setfill('0') << std::setw(3) << ms.count() << 'Z';
    return oss.str();
}

} // namespace

std::string canonical_dump(const json& j) {
    return j.dump(-1, ' ', /*ensure_ascii=*/true);
}

std::string sha256_hex(const std::string& data) {
    return picosha2::hash256_hex_string(data);
}

Audit::Audit(const std::string& path) : path_(path), prev_hash_(genesis_hash()), next_seq_(1) {
    load_tail();
}

void Audit::load_tail() {
    std::ifstream in(path_);
    if (!in.is_open()) return;
    std::string line, last_line;
    while (std::getline(in, line)) {
        if (!line.empty()) last_line = line;
    }
    if (last_line.empty()) return;
    try {
        json rec = json::parse(last_line);
        prev_hash_ = rec.at("hash").get<std::string>();
        next_seq_ = rec.at("seq").get<long long>() + 1;
    } catch (...) {
        // 마지막 줄이 손상됐다면 genesis부터 다시 쌓는다 (audit_verify가 이를 잡아낸다).
    }
}

long long Audit::record(const std::string& event, const json& payload) {
    long long seq = next_seq_;
    std::string ts = now_iso8601();
    std::string payload_canon = canonical_dump(payload);
    std::string material = prev_hash_ + "|" + std::to_string(seq) + "|" + ts + "|" + event + "|" + payload_canon;
    std::string hash = sha256_hex(material);

    json rec;
    rec["seq"] = seq;
    rec["ts"] = ts;
    rec["event"] = event;
    rec["payload"] = payload;
    rec["prev_hash"] = prev_hash_;
    rec["hash"] = hash;

    std::ofstream out(path_, std::ios::app);
    out << canonical_dump(rec) << "\n";
    out.flush();

    prev_hash_ = hash;
    next_seq_ = seq + 1;
    return seq;
}

json Audit::verify_chain() const {
    std::ifstream in(path_);
    json result;
    if (!in.is_open()) {
        result["valid"] = true;
        result["count"] = 0;
        return result;
    }

    std::string prev = genesis_hash();
    long long count = 0;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) continue;
        json rec;
        try {
            rec = json::parse(line);
        } catch (...) {
            result["valid"] = false;
            result["broken_at"] = count + 1;
            return result;
        }
        long long seq = rec.value("seq", (long long)0);
        std::string ts = rec.value("ts", "");
        std::string event = rec.value("event", "");
        json payload = rec.value("payload", json::object());
        std::string prev_hash = rec.value("prev_hash", "");
        std::string hash = rec.value("hash", "");

        if (prev_hash != prev) {
            result["valid"] = false;
            result["broken_at"] = seq;
            return result;
        }
        std::string material = prev_hash + "|" + std::to_string(seq) + "|" + ts + "|" + event + "|" + canonical_dump(payload);
        std::string recomputed = sha256_hex(material);
        if (recomputed != hash) {
            result["valid"] = false;
            result["broken_at"] = seq;
            return result;
        }
        prev = hash;
        count++;
    }

    result["valid"] = true;
    result["count"] = count;
    return result;
}
