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
