"""지능 계층 — LLM 호출 → 행동 명세(JSON) 생성.

I1 격리 규칙: 이 파일은 executor를 import하지 않는다. planner는 순수 함수에 가깝다 —
입력은 관측 텍스트, 출력은 JSON 문자열(을 파싱한 dict)뿐이다. 실행 API에 접근할 수 없다.
"""
import hashlib
import json
import os
import pathlib
import re

from agent.prompts import SYSTEM_PROMPT, build_user_message

DEFAULT_CACHE_DIR = pathlib.Path(__file__).resolve().parent / "cached_plans"


class PlannerError(Exception):
    pass


def _extract_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _cache_key(instruction: str) -> str:
    normalized = " ".join(instruction.strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class Planner:
    def __init__(self, api_key: str = None, model: str = "claude-sonnet-5",
                 cache_dir=None, offline: bool = False):
        self.model = model
        self.offline = offline
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None

    def plan(self, instruction: str, observation: dict, state_hint: dict, scenario: str = None) -> dict:
        """자연어 지시 -> 행동 명세(dict). scenario가 주어지면 그 이름으로 캐시를 직접 찾는다
        (--replay 데모용). 없으면 instruction 해시로 캐시를 찾는다."""
        key = f"scenario_{scenario}" if scenario else _cache_key(instruction)

        if self.offline:
            cached = self._load_cache(key)
            if cached is None:
                raise PlannerError(f"오프라인 모드인데 캐시된 응답이 없습니다: '{instruction}' (key={key})")
            return cached["spec"]

        user_msg = build_user_message(instruction, observation, state_hint)
        spec = self._call_and_parse(user_msg)
        self._save_cache(key, instruction, spec)
        return spec

    def _call_and_parse(self, user_msg: str) -> dict:
        last_err = None
        for _ in range(2):  # 최초 시도 + 재시도 1회. 그래도 실패하면 절대 추측해서 고치지 않는다.
            raw = self._call_llm(user_msg)
            try:
                cleaned = _extract_json(raw)
                spec = json.loads(cleaned)
                if not isinstance(spec, dict) or "steps" not in spec:
                    raise ValueError("응답에 steps가 없습니다")
                return spec
            except (json.JSONDecodeError, ValueError) as e:
                last_err = e
                continue
        raise PlannerError(f"AI 응답을 이해할 수 없어 중단했습니다: {last_err}")

    def _call_llm(self, user_msg: str) -> str:
        client = self._client_lazy()
        resp = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return resp.content[0].text

    def _client_lazy(self):
        if self._client is None:
            if not self.api_key:
                raise PlannerError("ANTHROPIC_API_KEY가 설정되어 있지 않습니다")
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _load_cache(self, key: str):
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_cache(self, key: str, instruction: str, spec: dict) -> None:
        self.cache_dir.mkdir(exist_ok=True)
        path = self.cache_dir / f"{key}.json"
        path.write_text(
            json.dumps({"user_instruction": instruction, "spec": spec}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
