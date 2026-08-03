"""Phase 6 DoD: 해시 체인 감사 로그 — 정상 / 중간 변조 / 줄 삭제 / 줄 삽입 4케이스."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.kernel_client import KernelClient
from tests.helpers import TEST_SECRET, make_attestation, make_spec

KERNEL_EXE = ROOT / "kernel" / "safehand_kernel.exe"
TEST_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "rules_test.json"


@pytest.fixture
def kernel_and_log(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    client = KernelClient(str(KERNEL_EXE), str(log_path), str(TEST_CONFIG_PATH), TEST_SECRET)
    yield client, log_path
    client.close()


def _populate(kernel):
    for i in range(3):
        att = make_attestation()
        spec = make_spec(f"chain-{i}", [{"seq": 1, "action": "navigate", "target": "/bills"}], att)
        out = kernel.call({"type": "verify", "spec": spec, "attestation": att})
        assert out["decision"] == "ALLOW"


def test_audit_chain_valid_when_untouched(kernel_and_log):
    kernel, log_path = kernel_and_log
    _populate(kernel)
    result = kernel.call({"type": "audit_verify"})
    assert result["valid"] is True
    line_count = len(log_path.read_text(encoding="utf-8").splitlines())
    assert result["count"] == line_count


def test_audit_chain_detects_middle_tampering(kernel_and_log):
    kernel, log_path = kernel_and_log
    _populate(kernel)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    target_idx = len(lines) // 2
    rec = json.loads(lines[target_idx])
    rec["payload"]["decision"] = "ALLOW" if rec["payload"].get("decision") != "ALLOW" else "DENY"
    lines[target_idx] = json.dumps(rec, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = kernel.call({"type": "audit_verify"})
    assert result["valid"] is False
    assert result["broken_at"] == rec["seq"]


def test_audit_chain_detects_deleted_line(kernel_and_log):
    kernel, log_path = kernel_and_log
    _populate(kernel)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 4
    del lines[2]  # 중간 한 줄을 통째로 삭제
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = kernel.call({"type": "audit_verify"})
    assert result["valid"] is False


def test_audit_chain_detects_inserted_line(kernel_and_log):
    kernel, log_path = kernel_and_log
    _populate(kernel)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    fake = {
        "seq": 999, "ts": "2026-01-01T00:00:00.000Z", "event": "VERDICT",
        "payload": {"request_id": "forged", "decision": "ALLOW"},
        "prev_hash": "0" * 64, "hash": "1" * 64,
    }
    lines.insert(2, json.dumps(fake, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = kernel.call({"type": "audit_verify"})
    assert result["valid"] is False


def test_boot_and_config_reload_events_include_config_hash(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    kernel = KernelClient(str(KERNEL_EXE), str(log_path), str(TEST_CONFIG_PATH), TEST_SECRET)
    try:
        kernel.call({"type": "reload_config"})
    finally:
        kernel.close()

    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    boot = next(l for l in lines if l["event"] == "BOOT")
    reload_ev = next(l for l in lines if l["event"] == "CONFIG_RELOAD")
    assert boot["payload"]["config_hash"]
    assert reload_ev["payload"]["config_hash"] == boot["payload"]["config_hash"]
