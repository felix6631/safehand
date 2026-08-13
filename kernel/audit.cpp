#include "audit.hpp"
#include <chrono>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <sstream>
#include "vendor/picosha2.h" // SHA-256 해시 계산 라이브러리

using json = nlohmann::json;

namespace {
/**  체인의 시작점(0번째) 해시. 첫 레코드는 이 값을 prev_hash로 사용 */
std::string genesis_hash() { return std::string(64, '0'); }

/**
 * 현재 시각을 ISO8601 형식 (UTC, 밀리초 포함)dmfh qusghks
 * 예 : "2026-08-10T05:21:11.123Z"
 */
std::string now_iso8601() {
    using namespace std::chrono;
    auto now = system_clock::now();
    auto ms = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    std::time_t t = system_clock::to_time_t(now);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &t);  /* why windows why */
#else
    gmtime_r(&t, &tm_utc);
#endif
    std::ostringstream oss;
    oss << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%S");
    oss << '.' << std::setfill('0') << std::setw(3) << ms.count() << 'Z';
    return oss.str();
}

} /* namespace */

/**
 * JSON을 항상 동일한 형태의 문자열로 직렬화
 * (해시 계산 대상이 실행 환경/순서에 관계없이 동일해야 하므로 정규화 필요)
 */ 
std::string canonical_dump(const json& j) {
    return j.dump(-1, ' ', /*ensure_ascii=*/true);
}

/** 입력 문자열의 SHA-256 해시를 16진수 문자열로 변환 */ 
std::string sha256_hex(const std::string& data) {
    return picosha2::hash256_hex_string(data);
}

/** 생성자 : 기존 로그 파일이 있으면 마지막 레코드 상태를 불러와 이어쓰기 준비 */
Audit::Audit(const std::string& path) : path_(path), prev_hash_(genesis_hash()), next_seq_(1) {
    load_tail();
}

/** 로그 파일의 마지막 줄만 읽어 prev_hash_/next_seq_ 상태를 복원 */
void Audit::load_tail() {
    std::ifstream in(path_);
    if (!in.is_open()) return;
    std::string line, last_line;
    while (std::getline(in, line)) {
        if (!line.empty()) last_line = line; /** 파일이 없으면 genesis 상태로 시작 */
    }
    if (last_line.empty()) return;
    try {
        json rec = json::parse(last_line);
        prev_hash_ = rec.at("hash").get<std::string>();
        next_seq_ = rec.at("seq").get<long long>() + 1;
    } catch (...) {
        /** 마지막 줄이 손상됐다면 genesis부터 다시 쌓는다 (audit_verify가 이를 잡아낸다). */
    }
}

/** 새 레코드 1건을 계산 후 파일에 append(추가만 함, 기존 줄은 수정 안함) */
long long Audit::record(const std::string& event, const json& payload) {
    long long seq = next_seq_;
    std::string ts = now_iso8601();
    std::string payload_canon = canonical_dump(payload);

    /**
     * prev_hash + seq + ts +event +payload를 연결해 해시 구성
     * 이전 레코드의 해시가 섞여 들어가므로 체인 형태가 성립
     */
    std::string material = prev_hash_ + "|" + std::to_string(seq) + "|" + ts + "|" + event + "|" + payload_canon;
    std::string hash = sha256_hex(material);

    json rec;
    rec["seq"] = seq;
    rec["ts"] = ts;
    rec["event"] = event;
    rec["payload"] = payload;
    rec["prev_hash"] = prev_hash_;
    rec["hash"] = hash;

    std::ofstream out(path_, std::ios::app); /* append 모드로만 열어 기존 내용 보존 */
    out << canonical_dump(rec) << "\n";
    out.flush();

    /* 다음 record() 호출을 위해 상태 갱신 */
    prev_hash_ = hash;
    next_seq_ = seq + 1;
    return seq;
}

/* 파일 전체를 순회하며 해시 체인의 무결성을 검증 */
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
            /* 해당 줄 자체가 유효한 JSON이 아님 => 손상/조작으로 판단 */
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

        /* 검증 1 : 저장된 prev_hash가 직전 레코드의 실제 hash와 일치하는지 */
        if (prev_hash != prev) {
            result["valid"] = false;
            result["broken_at"] = seq;
            return result;
        }
        /**
         * 검증 2 : 현재 레코드 내용으로 해시를 재게산했을 때 저장된 hash와 일치하는지
         * (일치하지 않으면 seq/ts/event/payload 중 하나가 변조된 것)
         */
        std::string material = prev_hash + "|" + std::to_string(seq) + "|" + ts + "|" + event + "|" + canonical_dump(payload);
        std::string recomputed = sha256_hex(material);
        if (recomputed != hash) {
            result["valid"] = false;
            result["broken_at"] = seq;
            return result;
        }
        prev = hash; /* 다음 반복에서 비교할 직전 해시 갱신 */
        count++;
    }

    result["valid"] = true;
    result["count"] = count;
    return result;
}
