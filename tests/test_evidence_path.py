"""Phase 5 DoD: evidence 경로 파서 단위 테스트 (key / key.sub / key[idx] / key[idx].sub).

kernel/state.cpp::resolve_evidence()는 C++ 내부 함수라 직접 호출할 수 없으므로,
실제 프로토콜(verify)을 통해 R5-b 판정으로 간접 검증한다 — 이 프로젝트 전체가
"진짜 커널을 통해서만 검증한다"는 원칙을 따르므로 여기서도 예외를 두지 않는다.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.kernel_client import KernelClient
from tests.helpers import TEST_SECRET, make_attestation, make_spec

KERNEL_EXE = ROOT / "kernel" / "safehand_kernel.exe"
TEST_CONFIG_PATH = pathlib.Path(__file__).resolve().parent / "fixtures" / "rules_test.json"

STATE_VIEW = {
    "page": "/bills",
    "balance": 300000,
    "daily_transferred": 0,
    "bills": [
        {"id": "b0", "payee": "한국전력공사", "amount": 52000},
        {"id": "b1", "payee": "서울도시가스", "amount": 38000},
    ],
    "registered_payees": ["한국전력공사", "서울도시가스", "김영희"],
    "form_fields": ["amount", "payee"],
}


@pytest.fixture
def kernel(tmp_path):
    client = KernelClient(str(KERNEL_EXE), str(tmp_path / "audit.jsonl"), str(TEST_CONFIG_PATH), TEST_SECRET)
    yield client
    client.close()


def verify_with_evidence(kernel, target, value, evidence):
    att = make_attestation(state_view=STATE_VIEW)
    step = {"seq": 1, "action": "input" if target == "amount" else "select", "target": target, "value": value}
    if evidence is not None:
        step["evidence"] = evidence
    spec = make_spec("evidence-test", [step], att)
    return kernel.call({"type": "verify", "spec": spec, "attestation": att})


@pytest.mark.parametrize("evidence,value", [
    ("bills[0].amount", "52000"),      # key[idx].sub
    ("bills[1].amount", "38000"),      # 다른 인덱스
    ("daily_transferred", "0"),        # 단순 key (per_tx_limit을 넘지 않는 값으로 R3와 분리)
])
def test_amount_evidence_paths_allow(kernel, evidence, value):
    out = verify_with_evidence(kernel, "amount", value, evidence)
    assert out["decision"] == "ALLOW", out


@pytest.mark.parametrize("evidence,value", [
    ("bills[0].payee", "한국전력공사"),      # key[idx].sub (문자열)
    ("registered_payees[2]", "김영희"),      # key[idx] (배열 원소가 바로 스칼라)
])
def test_payee_evidence_paths_allow(kernel, evidence, value):
    out = verify_with_evidence(kernel, "payee", value, evidence)
    assert out["decision"] == "ALLOW", out


def test_evidence_out_of_range_index_denies(kernel):
    out = verify_with_evidence(kernel, "amount", "52000", "bills[5].amount")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_evidence_nonexistent_key_denies(kernel):
    out = verify_with_evidence(kernel, "amount", "300000", "attacker.amount")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_evidence_nonexistent_nested_key_denies(kernel):
    out = verify_with_evidence(kernel, "payee", "한국전력공사", "bills[0].nonexistent")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_evidence_value_mismatch_denies(kernel):
    # 경로는 존재하지만 값이 다름 — 숨긴 배너로 지어낸 값을 그럴듯한 진짜 경로에 붙인 경우
    out = verify_with_evidence(kernel, "amount", "999999", "bills[0].amount")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_evidence_pointing_to_object_not_scalar_denies(kernel):
    # bills[0] 전체(객체)를 근거로 대면 어떤 스칼라 값과도 일치할 수 없다
    out = verify_with_evidence(kernel, "amount", "52000", "bills[0]")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_missing_evidence_field_denies(kernel):
    out = verify_with_evidence(kernel, "amount", "52000", evidence=None)
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_empty_evidence_string_denies(kernel):
    out = verify_with_evidence(kernel, "amount", "52000", evidence="")
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])


def test_malformed_bracket_syntax_denies(kernel):
    out = verify_with_evidence(kernel, "amount", "52000", "bills[0.amount")  # 닫는 괄호 없음
    assert out["decision"] == "DENY"
    assert any(t["rule_id"] == "R5" for t in out["triggered"])
