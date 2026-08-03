#include "ledger.hpp"
#include <chrono>
#include <cstdio>
#include <ctime>

namespace {

std::string today() {
    using namespace std::chrono;
    std::time_t t = system_clock::to_time_t(system_clock::now());
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &t);
#else
    gmtime_r(&t, &tm_utc);
#endif
    char buf[16];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02d", tm_utc.tm_year + 1900, tm_utc.tm_mon + 1, tm_utc.tm_mday);
    return std::string(buf);
}

} // namespace

void Ledger::roll_if_new_day() const {
    std::string t = today();
    if (t != date_) {
        date_ = t;
        daily_total_ = 0;
    }
}

long long Ledger::daily_total() const {
    roll_if_new_day();
    return daily_total_;
}

void Ledger::add(long long amount) {
    roll_if_new_day();
    daily_total_ += amount;
}
