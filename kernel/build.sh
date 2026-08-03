#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
g++ -std=c++17 -O2 -Wall -Wextra -static -o safehand_kernel \
    main.cpp schema.cpp rules.cpp audit.cpp config.cpp ledger.cpp state.cpp
echo "빌드 완료: kernel/safehand_kernel"
