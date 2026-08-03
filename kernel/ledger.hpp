#pragma once
#include <string>

// 일일 누적 이체/납부 금액 추적. 프로세스 메모리에만 있으며 날짜가 바뀌면 리셋된다.
// commit()으로 실제 집행된 금액만 누적한다 (verify만 통과하고 실행되지 않은 금액은 세지 않는다).

class Ledger {
public:
    long long daily_total() const;
    void add(long long amount);

private:
    mutable long long daily_total_ = 0;
    mutable std::string date_ = "";

    void roll_if_new_day() const;
};
