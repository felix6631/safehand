"""신뢰 경계 관리자 — planner / kernel / executor 세 계층을 다 아는 유일한 지점.

I2: planner는 커널 stdin 핸들을 갖지 않는다 (오직 orchestrator만 KernelClient를 가짐).
I3: attestation은 executor가 만들어 AI를 우회해 orchestrator를 통해 커널로 전달된다.
"""
from agent.executor import Executor
from agent.kernel_client import KernelClient
from agent import snapshot


class Orchestrator:
    def __init__(self, kernel: KernelClient, executor: Executor):
        self.kernel = kernel
        self.executor = executor
        self._pending_specs = {}  # request_id -> spec (HOLD 해소 후 실행하기 위해 보관)

    def run(self, spec: dict) -> dict:
        attestation = self.executor.attest()
        verdict = self.kernel.call({"type": "verify", "spec": spec, "attestation": attestation})

        if verdict["decision"] == "DENY":
            return {"status": "denied", "verdict": verdict}
        if verdict["decision"] == "HOLD":
            self._pending_specs[spec["request_id"]] = spec
            return {"status": "hold", "verdict": verdict}

        return self._execute_steps(spec, verdict)

    def resolve(self, request_id: str, challenge: str, approve: bool) -> dict:
        """사용자가 HOLD 확인 화면에서 승인/취소한 뒤 호출한다."""
        resp = self.kernel.call({
            "type": "resolve_hold",
            "request_id": request_id,
            "challenge": challenge,
            "decision": "approve" if approve else "cancel",
        })

        spec = self._pending_specs.pop(request_id, None)
        if resp["decision"] != "ALLOW" or spec is None:
            return {"status": "denied", "resolve": resp}

        return self._execute_steps(spec, resp)

    def _execute_steps(self, spec: dict, verdict: dict) -> dict:
        results = []
        for step in spec["steps"]:
            # TOCTOU 방어: 승인 시점과 실행 시점 사이에 화면이 바뀌었을 수 있다 — 매 스텝 직전 재확인.
            latest_attestation = self.executor.attest()
            check = self.kernel.call({
                "type": "step_check",
                "request_id": spec["request_id"],
                "seq": step["seq"],
                "attestation": latest_attestation,
            })
            if check["decision"] != "ALLOW":
                return {"status": "halted", "verdict": verdict, "step_check": check, "results": results}

            if step.get("irreversible"):
                snapshot.save(spec["request_id"], step["seq"])
            result = self.executor.act(step["action"], step["target"], step.get("value"))
            self.kernel.call({
                "type": "commit",
                "request_id": spec["request_id"],
                "seq": step["seq"],
                "result": result,
                "attestation": self.executor.attest(),
            })
            results.append({"seq": step["seq"], "result": result})

        return {"status": "executed", "verdict": verdict, "results": results}
