"""state_view의 정규 직렬화(canonical) 및 해시 계산.

이 모듈이 만들어내는 state_hash는 커널(R5-a)이 검증하는 '사실'의 지문입니다.
동일한 state_view는 항상 동일한 해시를, 한 글자라도 다르면 완전히 다른 해시를 내야 합니다.
"""
import hashlib
import hmac as _hmac
import json


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def state_hash(state_view: dict) -> str:
    return hashlib.sha256(canonical(state_view).encode("ascii")).hexdigest()


def sign(secret: str, state_view: dict) -> str:
    return _hmac.new(
        secret.encode("utf-8"), canonical(state_view).encode("ascii"), hashlib.sha256
    ).hexdigest()


def sign_instruction(secret: str, instruction: str) -> str:
    """사용자가 실제로 한 말에 대한 서명.

    state_view 서명(sign)과 분리해 둔다. 기존 서명 방식을 바꾸지 않아야 이미 검증된
    골든 케이스들이 그대로 유효하고, 이 필드가 없는 attestation도 예전처럼 동작한다.

    이 서명이 R5에 '사용자 발화'라는 두 번째 신뢰 경로를 열어준다. secret은 executor와
    커널만 알기 때문에, planner(AI)는 사용자가 하지 않은 말을 지어낼 수 없다.
    """
    return _hmac.new(
        secret.encode("utf-8"),
        canonical({"user_instruction": instruction}).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
