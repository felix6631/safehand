"""실행 계층 — 모의 사이트를 실제로 조작하고, 조작 전후의 '사실'을 증언(attestation)한다.

I1 격리 규칙: planner는 이 모듈을 import할 수 없다.
executor가 만드는 attestation은 orchestrator를 통해 AI를 우회해 커널로 전달된다 (I3).
"""
from datetime import datetime, timezone

import requests

from agent.attestation import sign, state_hash


class ExecutorError(Exception):
    pass


class Executor:
    def __init__(self, base_url: str, secret: str):
        self.base_url = base_url.rstrip("/")
        self.secret = secret

    def get_observation(self) -> dict:
        r = requests.get(f"{self.base_url}/api/observation", timeout=5)
        r.raise_for_status()
        return r.json()

    def get_state_view(self) -> dict:
        r = requests.get(f"{self.base_url}/api/state_view", timeout=5)
        r.raise_for_status()
        return r.json()

    def attest(self) -> dict:
        sv = self.get_state_view()
        return {
            "att_version": "1.0",
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "state_view": sv,
            "state_hash": state_hash(sv),
            "hmac": sign(self.secret, sv),
        }

    def act(self, action: str, target: str, value=None) -> dict:
        body = {"action": action, "target": target}
        if value is not None:
            body["value"] = value
        r = requests.post(f"{self.base_url}/api/act", json=body, timeout=5)
        if r.status_code != 200:
            raise ExecutorError(r.json().get("error", "실행 실패"))
        return r.json()

    def reset(self) -> dict:
        r = requests.post(f"{self.base_url}/api/reset", timeout=5)
        r.raise_for_status()
        return r.json()
