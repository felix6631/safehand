"""실행 계층 — 모의 사이트를 실제로 조작하고, 조작 전후의 '사실'을 증언(attestation)한다.

I1 격리 규칙: planner는 이 모듈을 import할 수 없다.
executor가 만드는 attestation은 orchestrator를 통해 AI를 우회해 커널로 전달된다 (I3).
"""
from datetime import datetime, timezone  # 증언을 만든 시각을 기록하는 데 사용.

import requests  # 모의 사이트와는 HTTP로 통신한다 (진짜 웹 조작과 같은 방식).

from agent.attestation import sign, sign_instruction, state_hash


class ExecutorError(Exception):
    """조작이 실패했을 때 던진다 (잔액 부족, 없는 버튼 등).

    이 예외가 올라가면 orchestrator가 남은 스텝을 실행하지 않는다.
    """
    pass


class Executor:
    def __init__(self, base_url: str, secret: str):
        # 주소 끝의 '/'를 떼어 둔다. 안 그러면 f-string으로 붙일 때 '//api/...'가 된다.
        self.base_url = base_url.rstrip("/")
        # 증언에 서명할 비밀키. 커널과 같은 값이어야 하고 AI 쪽에는 없어야 한다.
        self.secret = secret

    def get_observation(self) -> dict:
        """AI가 보는 화면 텍스트 전부. **오염될 수 있다.**

        숨겨진 인젝션 배너도 여기 그대로 들어온다. AI를 속이는 것이 바로 이 값이며,
        그래서 이 값은 커널의 판정 근거로 쓰지 않는다.
        """
        r = requests.get(f"{self.base_url}/api/observation", timeout=5)
        # 사이트가 오류를 냈으면 여기서 예외가 난다. 이상한 값을 그대로 AI에게 주지 않기 위해서다.
        r.raise_for_status()
        return r.json()

    def get_state_view(self) -> dict:
        """커널이 보는 구조화된 사실. **오염되지 않는다.**

        사이트 내부 상태에서 직접 읽은 값만 담기므로 화면에 무엇이 적혀 있든 영향이 없다.
        get_observation()과 이 값의 차이가 R5(상태·근거 대조)를 성립시킨다.
        """
        # 주소만 다를 뿐 위와 같은 요청이다. 같은 사이트가 '두 가지 진실'을 따로 내준다.
        r = requests.get(f"{self.base_url}/api/state_view", timeout=5)
        r.raise_for_status()
        return r.json()

    def attest(self, user_instruction: str = None) -> dict:
        """현재 화면의 '사실'을 증언한다.

        user_instruction이 주어지면 사용자가 실제로 한 말도 함께 증언한다. 이 값은
        planner를 거치지 않고 orchestrator에서 곧장 넘어오므로(I3), AI는 여기에
        손댈 수 없다 — 커널이 R5에서 금액의 근거로 쓸 수 있는 이유다.
        """
        # 증언의 뼈대가 되는 '사실'을 먼저 읽는다. 이 값 하나로 해시와 서명을 모두 만든다.
        sv = self.get_state_view()
        att = {
            "att_version": "1.0",  # 형식이 바뀌면 올린다. 커널이 버전을 보고 해석을 달리할 수 있다.
            # 언제 본 화면인지 UTC로 남긴다. 'Z'로 끝나는 표준 표기로 맞춰 커널이 읽기 쉽게 한다.
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "state_view": sv,                  # 사실 원본. 커널이 evidence 경로를 여기서 찾는다.
            "state_hash": state_hash(sv),      # 그 사실의 지문. AI의 주장과 대조한다(R5-a).
            "hmac": sign(self.secret, sv),     # 서명. 이 증언이 executor가 만든 것임을 증명한다.
        }
        # 사용자 발화는 있을 때만 넣는다. 시나리오 재생처럼 발화가 없는 경우도 있기 때문이다.
        if user_instruction:
            att["user_instruction"] = user_instruction
            # 발화에도 따로 서명한다. 서명이 없으면 커널이 이 값을 근거로 인정하지 않는다(R5-c).
            att["instruction_hmac"] = sign_instruction(self.secret, user_instruction)
        return att

    def act(self, action: str, target: str, value=None) -> dict:
        """실제로 화면을 조작한다. 커널이 ALLOW한 스텝만 여기까지 온다.

        여기서 실패하면(잔액 부족 등) 예외를 던져 나머지 스텝을 중단시킨다.
        절반만 실행된 상태로 끝나지 않게 하기 위해서다.
        """
        body = {"action": action, "target": target}
        # value는 input/select에만 있다. None을 그대로 보내면 사이트가 빈 값으로 착각할 수 있다.
        if value is not None:
            body["value"] = value
        r = requests.post(f"{self.base_url}/api/act", json=body, timeout=5)
        # 사이트가 거부한 이유(잔액 부족 등)를 그대로 꺼내 예외 메시지로 올린다.
        if r.status_code != 200:
            raise ExecutorError(r.json().get("error", "실행 실패"))
        return r.json()

    def reset(self) -> dict:
        """모의 사이트를 초기 상태로 되돌린다 (시연 준비용)."""
        r = requests.post(f"{self.base_url}/api/reset", timeout=5)
        r.raise_for_status()
        return r.json()
