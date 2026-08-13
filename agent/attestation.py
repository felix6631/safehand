"""state_view의 정규 직렬화(canonical) 및 해시 계산.

이 모듈이 만들어내는 state_hash는 커널(R5-a)이 검증하는 '사실'의 지문입니다.
동일한 state_view는 항상 동일한 해시를, 한 글자라도 다르면 완전히 다른 해시를 내야 합니다.
"""
import hashlib          # SHA-256 해시. 화면 상태의 지문을 만드는 데 쓴다.
import hmac as _hmac    # 비밀키가 있어야 만들 수 있는 서명. 위조 방지용.
import json             # dict를 문자열로 바꿀 때 사용.


def canonical(obj: dict) -> str:
    """dict를 '항상 똑같은 한 가지 문자열'로 만든다.

    같은 내용이면 언제 어디서 만들어도 바이트가 완전히 같아야 한다. 그래서 세 가지를 고정한다.
      sort_keys      키 순서가 달라도 같은 결과가 나오도록
      separators     공백을 넣지 않도록 ("a": 1 이 아니라 "a":1)
      ensure_ascii   한글을 \\uXXXX로 escape 해서 인코딩 차이를 없애도록

    커널의 canonical_dump()가 이것과 글자 하나까지 같은 결과를 내야 한다.
    한쪽만 바꾸면 모든 검증이 조용히 실패한다.
    """
    # 옵션 세 개가 모두 '결과를 하나로 못박기' 위한 것이다.
    # 하나라도 빠지면 같은 내용인데 다른 문자열이 나와 해시·서명이 어긋난다.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def state_hash(state_view: dict) -> str:
    """화면 상태의 지문. AI가 '지금 이 화면을 봤다'고 주장할 때 대조하는 값(R5-a)."""
    # canonical()로 문자열을 만든 뒤 해시한다. ensure_ascii=True라 안전하게 ascii로 인코딩된다.
    # 화면이 조금이라도 바뀌면 이 값이 완전히 달라지므로 '언제 본 화면인지'를 특정할 수 있다.
    return hashlib.sha256(canonical(state_view).encode("ascii")).hexdigest()


def sign(secret: str, state_view: dict) -> str:
    """화면 상태에 대한 서명.

    해시(state_hash)는 누구나 계산할 수 있지만 서명은 secret을 아는 쪽만 만들 수 있다.
    secret은 executor와 커널만 가지며 planner 프로세스에는 없다 — 그래서 AI는
    '내가 본 화면은 이랬다'는 증언을 위조할 수 없다.
    """
    return _hmac.new(
        # 첫 인자는 비밀키, 둘째는 서명 대상. 대상은 해시와 똑같이 canonical() 결과를 쓴다.
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
        # 문자열을 그냥 서명하지 않고 {"user_instruction": ...} 형태로 감싼다.
        # 커널도 똑같이 감싸서 계산하므로, 감싸는 모양이 서로 달라지면 검증이 실패한다.
        canonical({"user_instruction": instruction}).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
