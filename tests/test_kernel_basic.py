"""Phase 2 DoD: 커널 파이프 통신, R1 스키마 검증, 감사 로그 축적/검증."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent.kernel_client import KernelClient, KernelDeadError
from tests.helpers import TEST_SECRET, make_attestation, make_spec

ROOT = pathlib.Path(__file__).resolve().parent.parent
KERNEL_EXE = ROOT / "kernel" / "safehand_kernel.exe"
CONFIG_PATH = ROOT / "config" / "rules.json"


@pytest.fixture
def kernel(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    client = KernelClient(str(KERNEL_EXE), str(log_path), str(CONFIG_PATH), TEST_SECRET)
    yield client
    client.close()


def valid_request(request_id="req-1", action="navigate", target="/bills"):
    att = make_attestation()
    spec = make_spec(request_id, [{"seq": 1, "action": action, "target": target}], att)
    return {"type": "verify", "spec": spec, "attestation": att}


def test_ping_pong(kernel):
    assert kernel.call({"type": "ping"})["type"] == "pong"


def test_hundred_calls_no_hang(kernel):
    for _ in range(100):
        assert kernel.call({"type": "ping"})["type"] == "pong"


def test_malformed_json_denies_r1(kernel):
    with kernel.lock:
        kernel.proc.stdin.write("not valid json{{{\n")
        kernel.proc.stdin.flush()
        resp = kernel.proc.stdout.readline()
    import json
    out = json.loads(resp)
    assert out["decision"] == "DENY"
    assert out["triggered"][0]["rule_id"] == "R1"


def test_unregistered_action_denied(kernel):
    out = kernel.call(valid_request(action="download", target="malware.exe"))
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R1" for t in out["triggered"])


def test_valid_spec_allowed(kernel):
    out = kernel.call(valid_request())
    assert out["decision"] == "ALLOW"


def test_elapsed_us_present(kernel):
    out = kernel.call({"type": "ping"})
    assert "elapsed_us" in out and out["elapsed_us"] >= 0


def test_audit_log_accumulates_and_verifies(kernel, tmp_path):
    kernel.call(valid_request())
    kernel.call(valid_request(request_id="req-2"))
    log_path = tmp_path / "audit.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 3  # BOOT + 2 VERDICT

    result = kernel.call({"type": "audit_verify"})
    assert result["valid"] is True


def test_kernel_death_raises(kernel):
    kernel.proc.kill()
    kernel.proc.wait()
    with pytest.raises(KernelDeadError):
        kernel.call({"type": "ping"})
