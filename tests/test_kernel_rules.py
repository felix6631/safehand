"""Phase 4 DoD: R1~R4,R6~R9 골든 테스트, reload_config, HOLD/resolve_hold 흐름.
Phase 5 DoD: R5(상태·근거 대조) 골든 테스트는 tests/golden/R5_* 에 포함되어 test_golden으로 함께 돈다.
"""
import datetime
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.kernel_client import KernelClient
from tests.helpers import TEST_SECRET, make_attestation, make_spec

KERNEL_EXE = ROOT / "kernel" / "safehand_kernel.exe"
CONFIG_PATH = ROOT / "config" / "rules.json"
# 골든/규칙 테스트는 시각과 무관하게 결정적이어야 한다 — night_hours를 비활성화한 테스트 전용 설정을 쓴다.
# R9(심야) 자체를 검증하는 테스트만 예외적으로 실제 config/rules.json을 쓴다.
TEST_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "rules_test.json"
GOLDEN_DIR = pathlib.Path(__file__).resolve().parent / "golden"

CASES = sorted(p for p in GOLDEN_DIR.iterdir() if p.is_dir())


@pytest.fixture
def kernel(tmp_path):
    client = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit.jsonl"), str(TEST_CONFIG_PATH), TEST_SECRET)
    yield client
    client.close()


@pytest.fixture
def kernel_real_config(tmp_path):
    client = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit_real.jsonl"), str(CONFIG_PATH), TEST_SECRET)
    yield client
    client.close()


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_golden(kernel, case):
    inp = json.loads((case / "input.json").read_text(encoding="utf-8"))
    exp = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    out = kernel.call({"type": "verify", **inp})
    assert out["decision"] == exp["decision"], out
    if "rule_id" in exp:
        assert any(t["rule_id"] == exp["rule_id"] for t in out["triggered"]), out
    if exp["decision"] == "HOLD":
        assert out["challenge"]
    else:
        assert out["challenge"] is None


def _amount_att_and_spec(request_id, amount, balance=300000):
    """금액 스텝 하나짜리 spec/attestation을 R5(evidence)까지 통과하도록 만든다."""
    bills = [{"id": "bill_x", "payee": "한국전력공사", "amount": amount, "due": "2026-08-25", "paid": False}]
    att = make_attestation(balance=balance, bills=bills)
    spec = make_spec(request_id, [
        {"seq": 1, "action": "input", "target": "amount", "value": str(amount), "evidence": "bills[0].amount"},
    ], att)
    return att, spec


def test_r9_night_transfer_matches_current_hour(kernel_real_config):
    hour = datetime.datetime.now(datetime.timezone.utc).hour
    is_night = 0 <= hour < 6
    att, spec = _amount_att_and_spec("r9-dynamic", 10000)
    out = kernel_real_config.call({"type": "verify", "spec": spec, "attestation": att})
    if is_night:
        assert out["decision"] == "HOLD"
        assert any(t["rule_id"] == "R9" for t in out["triggered"])
    else:
        assert out["decision"] == "ALLOW"


def test_r3_daily_cumulative_limit_via_commit(kernel):
    # 하루 누적 한도(300,000원)를 commit으로 실제 채운 뒤, 다음 verify가 막히는지 확인한다.
    for i in range(1, 4):
        att, spec = _amount_att_and_spec(f"r3-daily-{i}", 80000)
        out = kernel.call({"type": "verify", "spec": spec, "attestation": att})
        assert out["decision"] == "ALLOW"
        kernel.call({"type": "commit", "request_id": f"r3-daily-{i}", "seq": 1, "result": {"ok": True}})

    # 누적 240,000 + 80,000 = 320,000 > daily_limit(300,000)이므로 네 번째는 막혀야 한다
    att4, spec4 = _amount_att_and_spec("r3-daily-4", 80000)
    out4 = kernel.call({"type": "verify", "spec": spec4, "attestation": att4})
    assert out4["decision"] == "DENY"
    assert any(t["rule_id"] == "R3" for t in out4["triggered"])


def test_reload_config_changes_limit_without_rebuild(kernel, tmp_path):
    att, spec = _amount_att_and_spec("reload-1", 60000)
    out_before = kernel.call({"type": "verify", "spec": spec, "attestation": att})
    assert out_before["decision"] == "ALLOW"

    base_rules = json.loads(TEST_CONFIG_PATH.read_text(encoding="utf-8"))
    custom_rules = dict(base_rules)
    custom_rules["per_tx_limit"] = 50000
    temp_config = tmp_path / "rules_lowered.json"
    temp_config.write_text(json.dumps(custom_rules, ensure_ascii=False), encoding="utf-8")

    kernel2 = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit2.jsonl"), str(temp_config), TEST_SECRET)
    try:
        out_low = kernel2.call({"type": "verify", "spec": spec, "attestation": att})
        assert out_low["decision"] == "DENY"  # 60,000 > 낮춘 한도 50,000

        temp_config.write_text(json.dumps(base_rules, ensure_ascii=False), encoding="utf-8")
        reload_resp = kernel2.call({"type": "reload_config"})
        assert reload_resp["type"] == "ok"

        out_after = kernel2.call({"type": "verify", "spec": spec, "attestation": att})
        assert out_after["decision"] == "ALLOW"  # 재빌드 없이 원래 한도로 복원됨
    finally:
        kernel2.close()


def _hold_att_and_spec(request_id):
    att = make_attestation()
    spec = make_spec(request_id, [{"seq": 1, "action": "click", "target": "btn_pay"}], att)
    return att, spec


def test_resolve_hold_without_challenge_is_rejected(kernel):
    att, spec = _hold_att_and_spec("hold-1")
    out = kernel.call({"type": "verify", "spec": spec, "attestation": att})
    assert out["decision"] == "HOLD"

    resp = kernel.call({"type": "resolve_hold", "request_id": "hold-1", "challenge": "", "decision": "approve"})
    assert resp["decision"] == "DENY"

    resp_wrong = kernel.call({
        "type": "resolve_hold", "request_id": "hold-1",
        "challenge": "not-the-real-challenge", "decision": "approve",
    })
    assert resp_wrong["decision"] == "DENY"


def test_resolve_hold_with_correct_challenge_approves(kernel):
    att, spec = _hold_att_and_spec("hold-2")
    out = kernel.call({"type": "verify", "spec": spec, "attestation": att})
    assert out["decision"] == "HOLD"
    challenge = out["challenge"]

    resp = kernel.call({
        "type": "resolve_hold", "request_id": "hold-2",
        "challenge": challenge, "decision": "approve",
    })
    assert resp["decision"] == "ALLOW"

    # 같은 challenge를 재사용하면 (이미 지워졌으므로) 다시 거부되어야 한다
    resp_again = kernel.call({
        "type": "resolve_hold", "request_id": "hold-2",
        "challenge": challenge, "decision": "approve",
    })
    assert resp_again["decision"] == "DENY"


def test_resolve_hold_cancel_denies(kernel):
    att, spec = _hold_att_and_spec("hold-3")
    out = kernel.call({"type": "verify", "spec": spec, "attestation": att})
    challenge = out["challenge"]

    resp = kernel.call({
        "type": "resolve_hold", "request_id": "hold-3",
        "challenge": challenge, "decision": "cancel",
    })
    assert resp["decision"] == "DENY"
