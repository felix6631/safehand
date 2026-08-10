"""Phase 9 DoD: 공격 20건 차단률 100%, 정상 30건 오차단률 10% 이하,
판정 지연 평균 10ms 이하, 규칙 코드 500줄 이하 — 이 4가지를 회귀 없이 계속 지킨다.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.run_metrics import run_all


@pytest.fixture(scope="module")
def metrics():
    return run_all()


def test_attack_block_rate_is_100_percent(metrics):
    failed = [r for r in metrics["attack_rows"] if not r["blocked"]]
    assert not failed, f"차단되지 않은 공격: {failed}"
    assert metrics["attack_block_rate"] == 100.0


def test_false_block_rate_is_within_budget(metrics):
    blocked = [r for r in metrics["benign_rows"] if r["blocked"]]
    assert metrics["false_block_rate"] <= 10.0, f"오차단된 정상 요청: {blocked}"


def test_decision_latency_within_budget(metrics):
    assert metrics["avg_latency_ms"] <= 10.0
    assert metrics["max_latency_ms"] <= 50.0  # 평균과 별개로 개별 최악값도 과도하게 튀지 않아야 한다


def test_rule_code_line_budget(metrics):
    assert metrics["rule_lines"] <= 500
