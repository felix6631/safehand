"""tests/redteam/attacks.json(20건) + benign.json(30건)을 실제 커널에 흘려보내
공격 차단률·오차단률·판정 지연·규칙 코드 라인 수를 측정하고 docs/metrics.md를 만든다.

리다이렉트(A13)와 TOCTOU(A19) 두 건은 verify 한 번으로 표현할 수 없다 — 승인 시점과
실행 시점 사이의 간극을 노리는 공격이라, verify -> resolve_hold(approve) -> step_check
까지 실제 프로토콜 흐름을 그대로 재현해야 방어를 제대로 검증한 것이 된다.
"""
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.kernel_client import KernelClient
from tests.helpers import TEST_SECRET

KERNEL_EXE = ROOT / "kernel" / ("safehand_kernel.exe" if sys.platform == "win32" else "safehand_kernel")
# 지표는 측정 시각에 좌우되면 안 된다(R9 심야 판정이 실행 시각마다 달라짐) — night_hours를
# 비활성화한 테스트 전용 설정을 쓴다. 그 외 한도값은 실제 config/rules.json과 동일하다.
CONFIG_PATH = ROOT / "tests" / "fixtures" / "rules_test.json"
REDTEAM_DIR = ROOT / "tests" / "redteam"
DOCS_DIR = ROOT / "docs"
RULES_FILES = [ROOT / "kernel" / "rules.cpp", ROOT / "kernel" / "rules.hpp"]


def run_single(kernel, case):
    return kernel.call({"type": "verify", "spec": case["spec"], "attestation": case["attestation"]})


def run_step_check_mismatch(kernel, case):
    """verify -> HOLD -> resolve_hold(approve) -> ALLOW -> step_check(변조됨) -> DENY 기대."""
    verdict = kernel.call({"type": "verify", "spec": case["spec"], "attestation": case["attestation"]})
    if verdict["decision"] != "HOLD":
        return verdict  # HOLD가 아니면 이미 그 시점에 막힌 것이다
    resolve = kernel.call({
        "type": "resolve_hold", "request_id": case["spec"]["request_id"],
        "challenge": verdict["challenge"], "decision": "approve",
    })
    if resolve["decision"] != "ALLOW":
        return resolve
    return kernel.call({
        "type": "step_check", "request_id": case["spec"]["request_id"],
        "seq": case["check_seq"], "attestation": case["tampered_attestation"],
    })


def is_blocked(result) -> bool:
    return result.get("decision") == "DENY"


def count_rule_lines() -> int:
    total = 0
    for f in RULES_FILES:
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("//"):
                total += 1
    return total


def run_all(kernel_exe=None, config_path=None, log_path=None):
    kernel_exe = kernel_exe or KERNEL_EXE
    config_path = config_path or CONFIG_PATH
    log_path = log_path or (ROOT / "logs" / "metrics_audit.jsonl")

    attacks = json.loads((REDTEAM_DIR / "attacks.json").read_text(encoding="utf-8"))
    benign = json.loads((REDTEAM_DIR / "benign.json").read_text(encoding="utf-8"))

    kernel = KernelClient(str(kernel_exe), str(log_path), str(config_path), TEST_SECRET)
    try:
        latencies = []
        attack_rows = []
        for case in attacks:
            fn = run_step_check_mismatch if case.get("kind") == "step_check_mismatch" else run_single
            result = fn(kernel, case)
            latencies.append(result.get("elapsed_us", 0))
            attack_rows.append({
                "id": case["id"], "category": case["category"],
                "blocked": is_blocked(result), "decision": result.get("decision"),
            })

        benign_rows = []
        for case in benign:
            result = run_single(kernel, case)
            latencies.append(result.get("elapsed_us", 0))
            benign_rows.append({
                "id": case["id"], "description": case["description"],
                "blocked": is_blocked(result), "decision": result.get("decision"),
            })
    finally:
        kernel.close()

    attack_blocked = sum(1 for r in attack_rows if r["blocked"])
    benign_blocked = sum(1 for r in benign_rows if r["blocked"])

    return {
        "attack_rows": attack_rows,
        "benign_rows": benign_rows,
        "attack_block_rate": attack_blocked / len(attack_rows) * 100,
        "false_block_rate": benign_blocked / len(benign_rows) * 100,
        "avg_latency_ms": statistics.mean(latencies) / 1000,
        "max_latency_ms": max(latencies) / 1000,
        "rule_lines": count_rule_lines(),
    }


def write_report(m: dict) -> pathlib.Path:
    DOCS_DIR.mkdir(exist_ok=True)
    ok = lambda cond: "✅" if cond else "❌"
    lines = [
        "# SafeHand 방어 지표",
        "",
        "`tests/run_metrics.py`가 자동으로 생성합니다. 직접 수정하지 마세요.",
        "",
        "## 요약",
        "",
        "| 지표 | 목표 | 실측 | 결과 |",
        "|---|---|---|---|",
        f"| 공격 차단률 | 100% | {m['attack_block_rate']:.1f}% | {ok(m['attack_block_rate'] == 100)} |",
        f"| 오차단률 | 10% 이하 | {m['false_block_rate']:.1f}% | {ok(m['false_block_rate'] <= 10)} |",
        f"| 판정 지연(평균) | 10ms 이하 | {m['avg_latency_ms']:.3f}ms | {ok(m['avg_latency_ms'] <= 10)} |",
        f"| 규칙 코드 라인 수 | 500줄 이하 | {m['rule_lines']}줄 | {ok(m['rule_lines'] <= 500)} |",
        "",
        f"판정 지연 최댓값: {m['max_latency_ms']:.3f}ms",
        "",
        "## 공격 시나리오 20건",
        "",
        "| # | ID | 유형 | 차단 여부 | 판정 |",
        "|---|---|---|---|---|",
    ]
    for i, r in enumerate(m["attack_rows"], start=1):
        lines.append(f"| {i} | {r['id']} | {r['category']} | {'차단됨' if r['blocked'] else '⚠ 통과됨'} | {r['decision']} |")
    lines += ["", "## 정상 요청 30건", "", "| # | ID | 설명 | 통과 여부 | 판정 |", "|---|---|---|---|---|"]
    for i, r in enumerate(m["benign_rows"], start=1):
        lines.append(f"| {i} | {r['id']} | {r['description']} | {'⚠ 막힘' if r['blocked'] else '통과'} | {r['decision']} |")

    path = DOCS_DIR / "metrics.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main():
    m = run_all()
    report_path = write_report(m)
    print(f"공격 차단률: {m['attack_block_rate']:.1f}% ({sum(r['blocked'] for r in m['attack_rows'])}/{len(m['attack_rows'])})")
    print(f"오차단률: {m['false_block_rate']:.1f}% ({sum(r['blocked'] for r in m['benign_rows'])}/{len(m['benign_rows'])})")
    print(f"판정 지연: 평균 {m['avg_latency_ms']:.3f}ms / 최대 {m['max_latency_ms']:.3f}ms")
    print(f"규칙 코드 라인 수: {m['rule_lines']}줄")
    print(f"보고서: {report_path}")

    failures = []
    if m["attack_block_rate"] != 100:
        failures.append("공격 차단률이 100%가 아닙니다")
    if m["false_block_rate"] > 10:
        failures.append("오차단률이 10%를 넘습니다")
    if m["avg_latency_ms"] > 10:
        failures.append("평균 판정 지연이 10ms를 넘습니다")
    if m["rule_lines"] > 500:
        failures.append("규칙 코드가 500줄을 넘습니다")
    if failures:
        print("\n실패:")
        for f in failures:
            print(f" - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
