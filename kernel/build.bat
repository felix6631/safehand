@echo off
cd /d "%~dp0"
g++ -std=c++17 -O2 -Wall -Wextra -static -o safehand_kernel.exe ^
    main.cpp schema.cpp rules.cpp audit.cpp config.cpp ledger.cpp state.cpp
echo 빌드 완료: kernel\safehand_kernel.exe
