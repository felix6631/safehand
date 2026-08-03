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
