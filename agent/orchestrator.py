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

    def plan_and_run(self, instruction: str, planner, scenario: str = None) -> dict:
        """자연어 지시를 받아 planner로 명세를 만들고 그대로 run()에 넘긴다.
        planner는 executor를 모른다 — observation/state_hint는 orchestrator가 만들어 건네준다."""
        observation = self.executor.get_observation()
        attestation = self.executor.attest()
        state_hint = {
            "page": attestation["state_view"]["page"],
            "balance": attestation["state_view"]["balance"],
            "state_hash": attestation["state_hash"],
        }
        spec = planner.plan(instruction, observation, state_hint, scenario=scenario)
        # 사용자의 원래 발화를 커널까지 그대로 들려보낸다. planner가 만든 spec과는
        # 별개의 경로이므로, AI가 사용자의 말을 바꿔치기할 수 없다 (I3).
        return self.run(spec, user_instruction=instruction)

    def run(self, spec: dict, user_instruction: str = None) -> dict:
        attestation = self.executor.attest(user_instruction=user_instruction)
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

            snap = None
            if step.get("irreversible"):
                snap = snapshot.save(spec["request_id"], step["seq"])
            result = self.executor.act(step["action"], step["target"], step.get("value"))
            commit_payload = {
                "type": "commit",
                "request_id": spec["request_id"],
                "seq": step["seq"],
                "result": result,
                "attestation": self.executor.attest(),
            }
            if snap is not None:
                commit_payload["snapshot"] = snap
            self.kernel.call(commit_payload)
            results.append({"seq": step["seq"], "result": result})

        return {"status": "executed", "verdict": verdict, "results": results}

    def undo(self, request_id: str) -> dict:
        """가장 최근 스냅샷으로 되돌린다. 되돌리기 자체도 감사 로그에 남는다."""
        snap_path = snapshot.latest_snapshot(request_id)
        if snap_path is None:
            return {"status": "no_snapshot"}

        restored_hash = snapshot.restore(snap_path)
        ack = self.kernel.call({
            "type": "undo",
            "request_id": request_id,
            "snapshot_path": str(snap_path),
            "snapshot_hash": restored_hash,
        })
        return {"status": "undone", "snapshot_path": str(snap_path), "ack": ack}
