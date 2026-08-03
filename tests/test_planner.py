"""Phase 7 DoD: planner의 JSON 파싱 안정성, 격리 규칙(I1), 오프라인 캐시 동작."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.planner import Planner, PlannerError

OBSERVATION = {"url": "/bills", "title": "청구서 목록", "raw_text": "청구서 목록", "elements": []}
STATE_HINT = {"page": "/bills", "balance": 300000, "state_hash": "abc123"}


def test_planner_does_not_import_executor_or_kernel_client():
    """I1/I2 격리 규칙: planner는 executor를 import할 수 없고, 커널 핸들을 갖지 않는다."""
    source = (ROOT / "agent" / "planner.py").read_text(encoding="utf-8")
    assert "import agent.executor" not in source
    assert "from agent.executor" not in source
    assert "import agent.kernel_client" not in source
    assert "from agent.kernel_client" not in source


def test_parses_clean_json_response(monkeypatch, tmp_path):
    planner = Planner(api_key="fake-key", cache_dir=tmp_path / "cache")
    monkeypatch.setattr(planner, "_call_llm", lambda msg: json.dumps({
        "schema_version": "1.0", "request_id": "r1", "steps": [],
    }))
    spec = planner.plan("아무 지시", OBSERVATION, STATE_HINT)
    assert spec["request_id"] == "r1"


def test_strips_markdown_code_fence(monkeypatch, tmp_path):
    planner = Planner(api_key="fake-key", cache_dir=tmp_path / "cache")
    fenced = "```json\n" + json.dumps({"schema_version": "1.0", "request_id": "r2", "steps": []}) + "\n```"
    monkeypatch.setattr(planner, "_call_llm", lambda msg: fenced)
    spec = planner.plan("아무 지시", OBSERVATION, STATE_HINT)
    assert spec["request_id"] == "r2"


def test_retries_once_then_succeeds(monkeypatch, tmp_path):
    planner = Planner(api_key="fake-key", cache_dir=tmp_path / "cache")
    calls = {"n": 0}

    def flaky(msg):
        calls["n"] += 1
        if calls["n"] == 1:
            return "이건 JSON이 아니라 그냥 텍스트입니다"
        return json.dumps({"schema_version": "1.0", "request_id": "r3", "steps": []})

    monkeypatch.setattr(planner, "_call_llm", flaky)
    spec = planner.plan("아무 지시", OBSERVATION, STATE_HINT)
    assert spec["request_id"] == "r3"
    assert calls["n"] == 2


def test_gives_up_after_two_failures_without_guessing(monkeypatch):
    planner = Planner(api_key="fake-key")
    monkeypatch.setattr(planner, "_call_llm", lambda msg: "여전히 JSON이 아닙니다")
    with pytest.raises(PlannerError):
        planner.plan("아무 지시", OBSERVATION, STATE_HINT)


def test_missing_steps_field_is_rejected(monkeypatch):
    planner = Planner(api_key="fake-key")
    monkeypatch.setattr(planner, "_call_llm", lambda msg: json.dumps({"request_id": "r4"}))
    with pytest.raises(PlannerError):
        planner.plan("아무 지시", OBSERVATION, STATE_HINT)


def test_no_api_key_raises_before_any_network_call():
    planner = Planner(api_key=None)
    # os.environ에도 키가 없다고 가정 (CI/테스트 환경에는 없음)
    import os
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.skip("환경에 ANTHROPIC_API_KEY가 설정되어 있어 이 테스트를 건너뜁니다")
    with pytest.raises(PlannerError):
        planner.plan("아무 지시", OBSERVATION, STATE_HINT)


def test_offline_mode_uses_cache_without_network(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "scenario_demo.json").write_text(json.dumps({
        "user_instruction": "데모 지시",
        "spec": {"schema_version": "1.0", "request_id": "cached-1", "steps": []},
    }, ensure_ascii=False), encoding="utf-8")

    planner = Planner(offline=True, cache_dir=cache_dir)
    spec = planner.plan("데모 지시", OBSERVATION, STATE_HINT, scenario="demo")
    assert spec["request_id"] == "cached-1"


def test_offline_mode_missing_cache_raises(tmp_path):
    planner = Planner(offline=True, cache_dir=tmp_path / "empty_cache")
    with pytest.raises(PlannerError):
        planner.plan("캐시에 없는 지시", OBSERVATION, STATE_HINT)


def test_live_call_saves_to_cache(monkeypatch, tmp_path):
    planner = Planner(api_key="fake-key", cache_dir=tmp_path / "cache")
    monkeypatch.setattr(planner, "_call_llm", lambda msg: json.dumps({
        "schema_version": "1.0", "request_id": "r5", "steps": [],
    }))
    planner.plan("자주 쓰는 지시", OBSERVATION, STATE_HINT)

    cached_files = list((tmp_path / "cache").glob("*.json"))
    assert len(cached_files) == 1
    cached = json.loads(cached_files[0].read_text(encoding="utf-8"))
    assert cached["spec"]["request_id"] == "r5"
