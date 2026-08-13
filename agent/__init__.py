"""세이프핸드의 파이썬 계층.

커널(C++)을 제외한 나머지가 전부 여기 있다. 파일별 역할:

    planner.py      지능 계층. LLM에게 계획을 받아온다. 신뢰하지 않는다.
    prompts.py      planner가 LLM에게 보내는 프롬프트.
    executor.py     실행 계층. 모의 사이트를 실제로 조작하고 '사실'을 증언한다.
    attestation.py  증언에 서명·해시를 붙인다. 커널과 계산식이 정확히 같아야 한다.
    kernel_client.py 커널(별도 실행 파일)과 표준 입출력으로 대화한다.
    orchestrator.py 위 넷을 잇는 신뢰 경계 관리자. 세 계층을 다 아는 유일한 지점.
    snapshot.py     실행 직전 상태 저장 / 되돌리기.
    audit_log.py    감사 로그를 사람이 읽을 요약으로 바꾼다.

지켜야 할 격리 규칙 세 가지 (이게 깨지면 프로젝트의 주장이 무너진다):

    I1  planner는 executor를 import하지 않는다. AI에게 실행 권한이 없어야 한다.
    I2  planner는 커널에게 직접 말할 수 없다. 커널의 입력은 orchestrator만 쥔다.
    I3  증언(attestation)은 executor가 만들어 AI를 우회해 커널로 간다.
        AI가 자기 행동의 근거를 스스로 지어낼 수 없어야 한다.
"""
