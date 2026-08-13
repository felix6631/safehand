#include "ledger.hpp"
#include <chrono>
#include <cstdio>
#include <ctime>

namespace {

/* 오늘 날짜를 "YYYY-MM-DD" 문자열로 변환 (UTC 기준) */
std::string today() {
    using namespace std::chrono;
    std::time_t t = system_clock::to_time_t(system_clock::now());
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &t); /* 윈도우: UTC 기준으로 시각 분해 */
#else
    gmtime_r(&t, &tm_utc); /* 리눅스/맥: 위와 동일(스레드 안전 버전) */
#endif
    char buf[16];
    /* tm_year는 1900년 기준 경과 연도, tm_mon은 0~11(1월=0)이라 각각 보정해서 출력 */
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02d", tm_utc.tm_year + 1900, tm_utc.tm_mon + 1, tm_utc.tm_mday);
    return std::string(buf);
}

} /* namespace */

/**
 * 오늘 날짜가 마지막으로 기록한 날짜(date_)와 다르면
 * 새로운 하루가 시작된 것으로 보고 누적액을 0으로 리셋한다.
 * 별도 타이머 없이, 호출될 때마다 날짜를 확인해서 필요하면 리셋하는 
 * "지연 초기화(lazy reset)" 방식 / 이게 일일 한도가 자정마다 자동 초기화되는 원리.
 * (date_/daily_total_는 헤더에서 mutable로 선언되어 있어 const 함수 안에서도 수정 가능)
 */
void Ledger::roll_if_new_day() const {
    std::string t = today();
    if (t != date_) {
        date_ = t;
        daily_total_ = 0;
    }
}

/**
 * 오늘 누적 이체액 조회. 조회 전에 항상 날짜부터 확인/리셋한다.
 * rules.cpp의 R3(일일 한도 검사)가 이 값을 참조한다.
 */
long long Ledger::daily_total() const {
    roll_if_new_day();
    return daily_total_;
}

/**
 * 이체 금액을 오늘 누적액에 더한다.
 * 주의: 이 함수는 "검증(verify)" 시점이 아니라 main.cpp의 handle_commit()에서,
 * 즉 실제로 스텝이 실행 완료된 시점에만 호출된다.
 * 검증 시점에 미리 더해버리면 실제로는 실행되지 않은(취소/거부된) 금액까지
 * 한도에서 깎여버리는 문제가 생기기 때문에, "검증 통과 != 실제 집행" 원칙에 따라
 * 반드시 실행이 끝난 뒤에만 반영한다.
 */
void Ledger::add(long long amount) {
    roll_if_new_day();
    daily_total_ += amount;
}
