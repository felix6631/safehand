"""Phase 3 DoD: LLM 없이 하드코딩 명세로 전체 파이프라인(executor+orchestrator+kernel) 검증."""
import json
import pathlib
import subprocess
import sys
import time

import pytest
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import audit_log
from agent.executor import Executor
from agent.kernel_client import KernelClient, KernelDeadError
from agent.orchestrator import Orchestrator
from tests.helpers import TEST_SECRET

KERNEL_EXE = ROOT / "kernel" / "safehand_kernel.exe"
CONFIG_PATH = ROOT / "config" / "rules.json"
MOCKSITE_URL = "http://localhost:5001"


@pytest.fixture(scope="module")
def mocksite_server():
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "mocksite" / "app.py")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            requests.get(MOCKSITE_URL + "/api/state_view", timeout=0.5)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)
    else:
        proc.terminate()
        raise RuntimeError("mocksite가 시작되지 않았습니다")
    yield
    proc.terminate()
    proc.wait()


@pytest.fixture
def fresh_state(mocksite_server):
    requests.post(MOCKSITE_URL + "/api/reset", timeout=5)
    yield


def load_spec_with_live_claim(executor: Executor) -> dict:
    spec = json.loads((ROOT / "tests" / "fixtures" / "spec_normal.json").read_text(encoding="utf-8"))
    att = executor.attest()
    spec["claimed_state"] = {
        "page": att["state_view"]["page"],
        "balance": att["state_view"]["balance"],
        "state_hash": att["state_hash"],
    }
    return spec


def test_e2e_normal_payment(fresh_state, tmp_path):
    kernel = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit.jsonl"), str(CONFIG_PATH), TEST_SECRET)
    executor = Executor(MOCKSITE_URL, secret=TEST_SECRET)
    orch = Orchestrator(kernel, executor)

    spec = load_spec_with_live_claim(executor)
    result = orch.run(spec)

    # btn_pay는 irreversible=true라 R2가 HOLD로 걸린다 — 사용자가 승인해야 진행된다.
    assert result["status"] == "hold"
    assert result["verdict"]["decision"] == "HOLD"
    challenge = result["verdict"]["challenge"]
    assert challenge

    result = orch.resolve(spec["request_id"], challenge, approve=True)

    assert result["status"] == "executed"

    sv = executor.get_state_view()
    assert sv["balance"] == 300000 - 52000
    assert sv["bills"][0]["paid"] is True

    snap_matches = list((ROOT / "snapshots").glob(f"{spec['request_id']}_*"))
    assert len(snap_matches) >= 1
    for p in snap_matches:
        p.unlink()

    kernel.close()


def test_toctou_state_change_blocks_step_check(fresh_state, tmp_path):
    """Phase 5 DoD: verify와 실행 사이에 mocksite 상태를 손으로 바꾸면 step_check에서 중단된다."""
    kernel = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit.jsonl"), str(CONFIG_PATH), TEST_SECRET)
    executor = Executor(MOCKSITE_URL, secret=TEST_SECRET)

    spec = load_spec_with_live_claim(executor)
    verdict = kernel.call({"type": "verify", "spec": spec, "attestation": executor.attest()})
    assert verdict["decision"] == "HOLD"  # btn_pay가 irreversible이라 R2가 먼저 HOLD시킨다

    resolve = kernel.call({
        "type": "resolve_hold", "request_id": spec["request_id"],
        "challenge": verdict["challenge"], "decision": "approve",
    })
    assert resolve["decision"] == "ALLOW"

    # 승인 후, 실행 전에 화면이 바뀐 상황을 흉내낸다 (공격자가 직접 mocksite를 건드린 경우)
    requests.post(MOCKSITE_URL + "/api/act", json={"action": "navigate", "target": "/transfer"}, timeout=5)

    tampered_attestation = executor.attest()
    check = kernel.call({
        "type": "step_check", "request_id": spec["request_id"],
        "seq": 2, "attestation": tampered_attestation,
    })
    assert check["decision"] == "DENY"

    sv = executor.get_state_view()
    assert sv["balance"] == 300000  # 아무 것도 실행되지 않았다

    kernel.close()


def test_undo_after_payment_restores_balance(fresh_state, tmp_path):
    """Phase 6 DoD: 납부 후 Undo -> 잔액이 정확히 원복되고, Undo 자체도 로그에 남는다."""
    log_path = tmp_path / "audit.jsonl"
    kernel = KernelClient(str(KERNEL_EXE), str(log_path), str(CONFIG_PATH), TEST_SECRET)
    executor = Executor(MOCKSITE_URL, secret=TEST_SECRET)
    orch = Orchestrator(kernel, executor)

    spec = load_spec_with_live_claim(executor)
    result = orch.run(spec)
    assert result["status"] == "hold"
    result = orch.resolve(spec["request_id"], result["verdict"]["challenge"], approve=True)
    assert result["status"] == "executed"

    sv = executor.get_state_view()
    assert sv["balance"] == 300000 - 52000
    assert sv["bills"][0]["paid"] is True

    undo_result = orch.undo(spec["request_id"])
    assert undo_result["status"] == "undone"

    sv_after_undo = executor.get_state_view()
    assert sv_after_undo["balance"] == 300000
    assert sv_after_undo["bills"][0]["paid"] is False

    summary = audit_log.summarize(log_path)
    entry = next(s for s in summary if s["request_id"] == spec["request_id"])
    assert entry["status"] == "되돌림"

    for p in (ROOT / "snapshots").glob(f"{spec['request_id']}_*"):
        p.unlink()
    kernel.close()


def test_kernel_death_halts_execution(fresh_state, tmp_path):
    kernel = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit.jsonl"), str(CONFIG_PATH), TEST_SECRET)
    executor = Executor(MOCKSITE_URL, secret=TEST_SECRET)
    orch = Orchestrator(kernel, executor)

    spec = load_spec_with_live_claim(executor)
    kernel.proc.kill()
    kernel.proc.wait()

    with pytest.raises(KernelDeadError):
        orch.run(spec)

    sv = executor.get_state_view()
    assert sv["balance"] == 300000  # 아무 것도 실행되지 않았어야 한다
