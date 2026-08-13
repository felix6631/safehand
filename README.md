# 세이프핸드 (SafeHand)

**AI가 사용자를 대신해 웹을 조작할 때, 그 AI를 신뢰하지 않는 전제로 모든 행동을 사전 검증하는 독립 안전 계층.**

AI 에이전트가 프롬프트 인젝션에 속아 잘못된 계좌로 돈을 보내려 해도, 별도 프로세스로 격리된 C++ 안전 커널(R1~R9)이 "그 값을 실제로 화면 어디서 봤는지" 근거를 요구합니다. 근거를 대지 못하면 — AI가 얼마나 정교하게 속았든 — 돈은 나가지 않습니다.

> 왜 이렇게 만들었는지, 무엇을 증명하려는지는 [safehand-introduce.md](safehand-introduce.md)에 있습니다.
> 각 Phase가 어떻게 구현됐는지는 [safehand-implementation-roadmap.md](safehand-implementation-roadmap.md)에 있습니다.

---

## 30초 요약

| 계층 | 언어 | 신뢰 | 역할 |
|---|---|---|---|
| 지능 계층 (planner) | Python | ✗ 신뢰 안 함 | 화면을 읽고 행동 계획(JSON)만 "제안" — 실행 권한 없음 |
| **안전 커널** | **C++** | ✓ | 별도 프로세스. R1~R9 규칙으로 ALLOW/HOLD/DENY 판정. 500줄 이내, 사람이 전부 읽을 수 있음 |
| 실행 계층 (executor) | Python | ✓ | 화면의 "사실"을 서명해 커널에 증언. AI는 이 값을 위조할 수 없음 |

핵심 규칙은 **R5**입니다: AI가 주장하는 금액·수취인은 반드시 화면(`state_view`)이나 서명된 사용자 발화(`user_instruction`) 중 하나로 근거를 대야 하며, 그 근거는 커널이 직접 재조회해 대조합니다. 프롬프트 인젝션으로 지어낸 계좌·금액은 그 어느 쪽에도 없으므로 근거를 댈 수 없습니다.

---

## 빠른 시작 (3분)

### 0. 준비물

| 도구 | 확인 명령 |
|---|---|
| Python 3.10+ | `python --version` |
| C++17 컴파일러 (g++) | `g++ --version` |
| Git | `git --version` |

Windows는 [MSYS2](https://www.msys2.org/) 설치 후 `pacman -S mingw-w64-x86_64-gcc`. macOS는 `xcode-select --install`. Linux는 `sudo apt install build-essential`. (자세한 설치 안내는 [safehand-implementation-roadmap.md](safehand-implementation-roadmap.md#1-개발-환경-구축-phase-0-a) 참고)

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 실행

```bash
python run_all.py --demo
```

모의 은행(`http://127.0.0.1:5001`)과 사용자 화면(`http://127.0.0.1:5000`)이 함께 뜨고, 커널이 자동으로 빌드되며, 브라우저가 사용자 화면으로 열립니다. `ANTHROPIC_API_KEY` 없이도 1~3막 시연 버튼은 저장된 응답을 재생하며 바로 동작합니다 (자유 입력을 실제 LLM으로 쓰려면 `.env.example`을 `.env`로 복사해 키를 넣으세요).

### 3. 시연 시나리오 클릭

- **1막 · 정상 납부** — AI가 전기요금을 정상적으로 냅니다.
- **2막 · AI가 실수** — AI가 금액을 잘못 읽지만, 돈은 나가지 않습니다.
- **3막 · 인젝션 공격** — 화면에 숨긴 지시문에 AI가 완전히 속지만, 공격자 계좌로는 한 푼도 나가지 않습니다.

대사와 타이밍이 적힌 시연 대본은 [docs/demo-script.md](docs/demo-script.md), 실제 실행 검증 기록은 [docs/verification-log.md](docs/verification-log.md), 방어 지표는 [docs/metrics.md](docs/metrics.md)에 있습니다.

### 시연 중 문제가 생기면

```bash
python run_all.py --replay 1   # 또는 2, 3
```

브라우저 없이 터미널에서 곧장 해당 막을 재생합니다. 시연 중 브라우저가 말썽이면 이걸로 대체하세요.

---

## 테스트

```bash
python -m pytest tests/ -v
```

커널 골든 테스트, evidence 경로 파서, 감사 로그 해시 체인, 레드팀 20건/정상 30건 방어 지표까지 전부 포함해 90여 개 테스트가 자동으로 커널을 실제로 실행하며 검증합니다. 방어 지표만 다시 뽑으려면:

```bash
python tests/run_metrics.py   # docs/metrics.md 재생성
```

---

## 프로젝트 구조

```
safehand/
├── kernel/          # 안전 커널 (C++) — R1~R9, 해시 체인 감사 로그
├── agent/           # 지능 계층(planner) + 실행 계층(executor) + orchestrator
├── mocksite/        # 모의 은행 (AI가 조작할 대상 — 안전장치 없음, 일부러)
├── ui/              # 사용자 화면 — 판정 결과를 큰 글씨/음성으로 보여줌
├── config/          # 임계값 (한도·신뢰도·심야 시간 등) — 재빌드 없이 조정 가능
├── tests/           # 골든 테스트, 레드팀 20건/정상 30건, 오프라인 3막 재생
├── docs/            # 방어 지표, 시연 대본, 실행 검증 기록
└── run_all.py       # mocksite + ui + 커널 빌드를 한 번에
```

각 계층이 왜 분리되어 있는지, R1-R9가 각각 무엇을 막는지는 [safehand-introduce.md](safehand-introduce.md)의 2-4절에 자세히 설명되어 있습니다.

---

## 자주 나오는 질문

| 질문 | 답변 요지 |
|---|---|
| 실제 은행에 어떻게 붙이나요? | 커널은 웹사이트가 아니라 **행동 명세**를 검증합니다. executor만 실제 브라우저 자동화로 바꾸면 커널은 그대로입니다. |
| 규칙이 너무 단순한 것 아닌가요? | 단순함이 설계 목표입니다. "검증할 수 없는 것으로 검증하지 않는다"는 원칙이며, 500줄 제한을 CI/테스트가 강제합니다. |
| AI가 커널을 속이면요? | 커널은 AI의 주장을 근거로 판정하지 않습니다. 실행 계층의 서명된 증언과 대조하며, AI는 그 서명 키에 접근할 수 없습니다. |
| AI가 커널을 우회하면요? | 커널은 별도 프로세스이고, 실행 API 핸들은 orchestrator만 가집니다. planner는 executor를 import조차 하지 않습니다. |
