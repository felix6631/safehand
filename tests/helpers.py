"""테스트 전용 spec/attestation 생성 헬퍼.

R5가 활성화된 이후로는 claimed_state.state_hash와 attestation.hmac이 실제로 맞아야
ALLOW/HOLD가 나온다 (틀리면 그 자체가 R5 DENY 테스트가 되어버린다) — 그래서 프로토콜과
동일한 계산 방식(agent.attestation)을 그대로 재사용한다.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.attestation import sign, state_hash

TEST_SECRET = "test-secret-not-for-prod"
DEFAULT_PAYEES = ["한국전력공사", "서울도시가스", "김영희"]


def make_state_view(page="/bills", balance=300000, daily_transferred=0,
                     bills=None, registered_payees=None, form_fields=None):
    return {
        "page": page,
        "balance": balance,
        "daily_transferred": daily_transferred,
        "bills": bills if bills is not None else [],
        "registered_payees": registered_payees if registered_payees is not None else DEFAULT_PAYEES,
        "form_fields": form_fields if form_fields is not None else [],
    }


def make_attestation(state_view=None, secret=TEST_SECRET, **state_view_kwargs):
    sv = state_view if state_view is not None else make_state_view(**state_view_kwargs)
    return {
        "att_version": "1.0",
        "captured_at": "2026-08-03T00:00:00.000Z",
        "state_view": sv,
        "state_hash": state_hash(sv),
        "hmac": sign(secret, sv),
    }


def make_spec(request_id, steps, attestation, confidence=0.9):
    sv = attestation["state_view"]
    return {
        "schema_version": "1.0",
        "request_id": request_id,
        "user_intent": "test",
        "model_confidence": confidence,
        "claimed_state": {
            "page": sv["page"],
            "balance": sv["balance"],
            "state_hash": attestation["state_hash"],
        },
        "steps": steps,
    }
