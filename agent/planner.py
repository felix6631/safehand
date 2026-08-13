"""지능 계층 — LLM 호출 → 행동 명세(JSON) 생성.

I1 격리 규칙: 이 파일은 executor를 import하지 않는다. planner는 순수 함수에 가깝다 —
입력은 관측 텍스트, 출력은 JSON 문자열(을 파싱한 dict)뿐이다. 실행 API에 접근할 수 없다.
"""
import hashlib   # 지시문을 캐시 파일 이름으로 바꿀 때 사용.
import json      # LLM 응답(JSON 문자열)을 dict로 읽고, 캐시로 저장할 때 사용.
import os        # 환경변수에서 API 키를 읽는다.
import pathlib   # 캐시 폴더 경로 계산.
import re        # 응답을 감싼 ``` 표시를 벗겨낼 때 사용.

from agent.prompts import SYSTEM_PROMPT, build_user_message

# 이 파일(agent/planner.py) 기준으로 agent/cached_plans/ 를 가리킨다.
# 실행 위치가 어디든 같은 폴더를 보도록 절대경로로 만든다.
DEFAULT_CACHE_DIR = pathlib.Path(__file__).resolve().parent / "cached_plans"


class PlannerError(Exception):
    """AI에게서 쓸 만한 계획을 받지 못했을 때 던진다.

    이 예외가 나면 실행은 시작조차 하지 않는다.
    """
    pass


def _extract_json(text: str) -> str:
    """모델이 JSON을 ```로 감싸 보내는 경우가 있어 그 껍데기만 벗긴다.

    내용은 절대 손대지 않는다. 형식이 깨졌으면 고치려 하지 말고 실패해야 한다.
    """
    text = text.strip()                              # 앞뒤 공백·줄바꿈 제거.
    text = re.sub(r"^```(?:json)?\s*", "", text)     # 맨 앞의 ``` 또는 ```json 제거.
    text = re.sub(r"\s*```$", "", text)              # 맨 뒤의 ``` 제거.
    return text.strip()


def _cache_key(instruction: str) -> str:
    """같은 지시는 같은 캐시 파일을 쓰도록 지시문을 해시로 바꾼다.

    공백만 다른 문장("전기요금  내줘" / "전기요금 내줘")을 같게 보려고 먼저 정규화한다.
    """
    # split()은 연속된 공백을 모두 잘라내므로, 다시 한 칸씩으로 이어 붙이면 표기가 통일된다.
    normalized = " ".join(instruction.strip().split())
    # 해시 전체는 너무 길어 파일 이름으로 불편하다. 앞 16자만 써도 충돌 걱정은 사실상 없다.
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class Planner:
    """LLM에게 계획을 받아오는 층.

    offline=True면 API를 부르지 않고 저장해 둔 계획을 재생한다. 시연이 네트워크나
    모델 상태에 흔들리지 않게 하려는 장치이며, 1~3막은 항상 이 모드로 돈다.
    """

    def __init__(self, api_key: str = None, model: str = "claude-sonnet-5",
                 cache_dir=None, offline: bool = False):
        self.model = model      # 어떤 모델을 쓸지. 바꿔도 커널의 R1~R9는 그대로 동작한다.
        self.offline = offline  # True면 API를 부르지 않고 캐시만 읽는다.
        # 테스트에서는 임시 폴더를 넘겨 실제 캐시를 건드리지 않게 한다.
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        # 인자로 받은 키를 우선하고, 없으면 환경변수(.env로 올라온 값)를 쓴다.
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None  # 실제 API 클라이언트. 처음 쓸 때 만든다(_client_lazy 참고).

    def plan(self, instruction: str, observation: dict, state_hint: dict, scenario: str = None) -> dict:
        """자연어 지시 -> 행동 명세(dict). scenario가 주어지면 그 이름으로 캐시를 직접 찾는다
        (--replay 데모용). 없으면 instruction 해시로 캐시를 찾는다."""
        # 시나리오 이름이 있으면 파일명이 정해져 있으므로 지시문과 무관하게 그 파일을 쓴다.
        key = f"scenario_{scenario}" if scenario else _cache_key(instruction)

        if self.offline:
            cached = self._load_cache(key)
            # 캐시가 없으면 여기서 멈춘다. 오프라인인데 몰래 API를 부르는 일은 없어야 한다.
            if cached is None:
                raise PlannerError(f"오프라인 모드인데 캐시된 응답이 없습니다: '{instruction}' (key={key})")
            return cached["spec"]

        # 여기부터는 실제 호출 경로. 화면 정보와 지시를 하나의 사용자 메시지로 만든다.
        user_msg = build_user_message(instruction, observation, state_hint)
        spec = self._call_and_parse(user_msg)
        # 성공한 응답은 저장해 둔다. 같은 지시를 오프라인으로 재생할 수 있게 된다.
        self._save_cache(key, instruction, spec)
        return spec

    def _call_and_parse(self, user_msg: str) -> dict:
        """LLM 응답을 JSON으로 읽는다. 못 읽으면 중단한다.

        여기서 응답을 손봐서 살려내면 안 된다. 형식이 깨진 응답을 사람이 고쳐 넣는 순간
        '커널이 검사한 계획'과 'AI가 만든 계획'이 달라지기 때문이다.
        """
        last_err = None
        for _ in range(2):  # 최초 시도 + 재시도 1회. 그래도 실패하면 절대 추측해서 고치지 않는다.
            raw = self._call_llm(user_msg)
            try:
                cleaned = _extract_json(raw)   # ``` 껍데기만 벗긴다.
                spec = json.loads(cleaned)     # 문자열을 dict로. 형식이 깨졌으면 여기서 예외.
                # 최소한의 모양은 여기서 본다. steps가 없으면 커널에 보낼 것도 없다.
                if not isinstance(spec, dict) or "steps" not in spec:
                    raise ValueError("응답에 steps가 없습니다")
                return spec
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e   # 마지막 실패 이유를 기억해 두고
                continue       # 한 번 더 시도한다.
        raise PlannerError(f"AI 응답을 이해할 수 없어 중단했습니다: {last_err}")

    def _call_llm(self, user_msg: str) -> str:
        """모델을 실제로 호출하고 응답 '텍스트'만 꺼내 온다."""
        client = self._client_lazy()
        resp = client.messages.create(
            model=self.model,
            # 최신 모델은 thinking을 따로 끄지 않으면 적응형 사고가 켜지고,
            # max_tokens가 '사고 토큰 + 응답'을 함께 제한한다. 1024로는 명세 JSON이
            # 중간에 잘려 파싱에 실패할 수 있어 넉넉히 잡는다.
            max_tokens=4096,
            system=SYSTEM_PROMPT,   # 지켜야 할 형식과 규칙. 매 호출 동일하다.
            messages=[{"role": "user", "content": user_msg}],  # 이번 화면과 이번 지시.
        )
        # content[0]이 항상 텍스트인 것은 아니다 — 사고가 켜지면 thinking 블록이 먼저 온다.
        # thinking 블록에는 .text가 없으므로 반드시 종류를 보고 골라야 한다.
        text = next((b.text for b in resp.content if b.type == "text"), None)
        # 텍스트가 하나도 없으면(길이 초과나 거부 등) 이유를 담아 실패시킨다.
        if text is None:
            raise PlannerError(f"AI 응답에 텍스트가 없습니다 (stop_reason={resp.stop_reason})")
        return text

    def _client_lazy(self):
        """API 클라이언트를 실제로 쓸 때 처음 한 번만 만든다.

        anthropic 패키지를 파일 맨 위가 아니라 여기서 import하는 이유:
        오프라인 재생만 할 때는 그 패키지가 설치되어 있지 않아도 동작해야 한다.
        """
        if self._client is None:
            # 키가 없으면 호출 자체가 불가능하므로 미리 알아보기 쉬운 메시지로 멈춘다.
            if not self.api_key:
                raise PlannerError("ANTHROPIC_API_KEY가 설정되어 있지 않습니다")
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _load_cache(self, key: str):
        """저장해 둔 계획을 읽는다. 없으면 None."""
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_cache(self, key: str, instruction: str, spec: dict) -> None:
        """받은 계획을 파일로 남긴다. 나중에 같은 지시를 오프라인으로 재생할 수 있다."""
        self.cache_dir.mkdir(exist_ok=True)  # 폴더가 없으면 만든다.
        path = self.cache_dir / f"{key}.json"
        path.write_text(
            # 어떤 지시에 대한 계획인지 함께 남긴다. 파일명이 해시라 이게 없으면 사람이 못 알아본다.
            # ensure_ascii=False + indent=2 는 사람이 열어볼 파일이므로 읽기 쉽게 하려는 것이다.
            json.dumps({"user_instruction": instruction, "spec": spec}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
