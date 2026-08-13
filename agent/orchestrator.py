"""신뢰 경계 관리자 — planner / kernel / executor 세 계층을 다 아는 유일한 지점.

전체 흐름 한눈에 보기:

    사용자 지시
        -> planner   : LLM이 계획(spec)을 만든다.        [신뢰 안 함]
        -> executor  : 지금 화면의 사실을 증언한다.        [신뢰함]
        -> kernel    : spec과 증언을 대조해 판정한다.
             DENY  -> 아무것도 실행하지 않고 끝
             HOLD  -> 사용자에게 물어본 뒤 resolve()로 재개
             ALLOW -> 스텝을 하나씩 실행
        -> executor  : 실제 조작. 스텝마다 커널에 다시 확인받는다.

I2: planner는 커널 stdin 핸들을 갖지 않는다 (오직 orchestrator만 KernelClient를 가짐).
I3: attestation은 executor가 만들어 AI를 우회해 orchestrator를 통해 커널로 전달된다.
"""
from agent.executor import Executor
from agent.kernel_client import KernelClient
from agent import snapshot


class Orchestrator:
    def __init__(self, kernel: KernelClient, executor: Executor):
        self.kernel = kernel      # 커널로 가는 유일한 통로. planner에게는 절대 넘기지 않는다(I2).
        self.executor = executor  # 실제 조작과 증언을 담당.
        # HOLD로 멈춘 계획을 잠시 보관한다. 사용자가 승인하면 이 계획을 그대로 실행한다.
        # AI에게 다시 물어보지 않는 이유: 사용자가 승인한 것과 다른 계획이 실행되면 안 된다.
        self._pending_specs = {}  # request_id -> spec

    def plan_and_run(self, instruction: str, planner, scenario: str = None) -> dict:
        """자연어 지시를 받아 planner로 명세를 만들고 그대로 run()에 넘긴다.
        planner는 executor를 모른다 — observation/state_hint는 orchestrator가 만들어 건네준다."""
        # AI에게 보여줄 화면 텍스트. 인젝션이 섞여 있을 수 있는 '오염 가능한' 값이다.
        observation = self.executor.get_observation()
        # 같은 순간의 '사실'. 여기서는 state_hash를 뽑아 AI에게 알려주는 용도로만 쓴다.
        attestation = self.executor.attest()
        # AI가 claimed_state에 그대로 옮겨 적어야 하는 값들. 틀리면 커널이 R5-a로 막는다.
        state_hint = {
            "page": attestation["state_view"]["page"],
            "balance": attestation["state_view"]["balance"],
            "state_hash": attestation["state_hash"],
        }
        # 여기서 AI가 계획을 만든다. 이 결과는 아직 아무 권한도 없는 '제안'일 뿐이다.
        spec = planner.plan(instruction, observation, state_hint, scenario=scenario)
        # 사용자의 원래 발화를 커널까지 그대로 들려보낸다. planner가 만든 spec과는
        # 별개의 경로이므로, AI가 사용자의 말을 바꿔치기할 수 없다 (I3).
        return self.run(spec, user_instruction=instruction)

    def run(self, spec: dict, user_instruction: str = None) -> dict:
        """계획을 커널에 제출하고, 판정에 따라 실행하거나 멈춘다.

        판정을 받기 전에는 어떤 조작도 하지 않는다. 이 순서가 이 프로젝트의 전부다.
        """
        # 판정 직전의 화면을 다시 증언한다. 계획을 만든 뒤 화면이 바뀌었을 수 있기 때문이다.
        attestation = self.executor.attest(user_instruction=user_instruction)
        # 커널에게 'AI의 주장(spec)'과 '시스템의 사실(attestation)'을 함께 넘겨 대조시킨다.
        verdict = self.kernel.call({"type": "verify", "spec": spec, "attestation": attestation})

        # 거부: 여기서 그냥 돌아간다. executor는 호출조차 되지 않으므로 아무 일도 일어나지 않는다.
        if verdict["decision"] == "DENY":
            return {"status": "denied", "verdict": verdict}
        if verdict["decision"] == "HOLD":
            # 사용자 확인이 필요하다. 계획을 보관해 두고 resolve()를 기다린다.
            self._pending_specs[spec["request_id"]] = spec
            return {"status": "hold", "verdict": verdict}

        # 남은 경우는 ALLOW뿐이다. 이때만 실제 조작으로 넘어간다.
        return self._execute_steps(spec, verdict)

    def resolve(self, request_id: str, challenge: str, approve: bool) -> dict:
        """사용자가 HOLD 확인 화면에서 승인/취소한 뒤 호출한다.

        challenge는 커널이 HOLD와 함께 내준 일회용 값이다. 이걸 되돌려줘야 승인이 성립하므로,
        커널이 멈춘 적 없는 요청을 누군가 '승인됐다'고 우길 수 없다.
        """
        # 승인 여부를 커널에 알린다. 최종 판정은 여기서도 커널이 내린다.
        resp = self.kernel.call({
            "type": "resolve_hold",
            "request_id": request_id,
            "challenge": challenge,
            "decision": "approve" if approve else "cancel",
        })

        # 보관해 둔 계획을 꺼내면서 목록에서 지운다. 같은 승인을 두 번 쓰지 못하게 하기 위해서다.
        spec = self._pending_specs.pop(request_id, None)
        # 커널이 승인하지 않았거나 보관된 계획이 없으면 실행하지 않는다.
        if resp["decision"] != "ALLOW" or spec is None:
            return {"status": "denied", "resolve": resp}

        return self._execute_steps(spec, resp)

    def _execute_steps(self, spec: dict, verdict: dict) -> dict:
        """승인된 계획을 한 스텝씩 실행한다.

        스텝마다 (1) 커널에 재확인 (2) 되돌릴 수 없는 행동이면 스냅샷 저장 (3) 실행
        (4) 결과를 커널에 기록, 순서를 지킨다. 중간에 한 번이라도 막히면 거기서 멈춘다.
        """
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
            # 재확인에서 막히면 이미 끝난 스텝은 그대로 두고 즉시 멈춘다.
            # 여기까지의 결과(results)를 함께 돌려줘야 무엇이 실행됐는지 사용자에게 보여줄 수 있다.
            if check["decision"] != "ALLOW":
                return {"status": "halted", "verdict": verdict, "step_check": check, "results": results}

            snap = None
            # 되돌릴 수 없는 행동이면 '실행 직전' 상태를 먼저 저장한다. 순서가 바뀌면 되돌릴 수 없다.
            if step.get("irreversible"):
                snap = snapshot.save(spec["request_id"], step["seq"])
            # 실제 조작. 이 줄이 이 프로젝트에서 유일하게 돈을 움직이는 지점이다.
            result = self.executor.act(step["action"], step["target"], step.get("value"))
            # 무엇을 했는지 커널에 알려 감사 로그에 남긴다. 실행 후의 화면도 함께 증언한다.
            commit_payload = {
                "type": "commit",
                "request_id": spec["request_id"],
                "seq": step["seq"],
                "result": result,
                "attestation": self.executor.attest(),
            }
            # 스냅샷을 떴다면 그 경로와 지문도 기록한다. 나중에 파일이 바뀌었는지 대조할 수 있다.
            if snap is not None:
                commit_payload["snapshot"] = snap
            self.kernel.call(commit_payload)
            results.append({"seq": step["seq"], "result": result})

        return {"status": "executed", "verdict": verdict, "results": results}

    def undo(self, request_id: str) -> dict:
        """가장 최근 스냅샷으로 되돌린다. 되돌리기 자체도 감사 로그에 남는다."""
        snap_path = snapshot.latest_snapshot(request_id)
        # 비가역 스텝이 없었으면 스냅샷도 없다. 되돌릴 게 없다는 뜻이므로 조용히 알린다.
        if snap_path is None:
            return {"status": "no_snapshot"}

        # 파일을 되돌리고 그 지문을 받는다.
        restored_hash = snapshot.restore(snap_path)
        # 되돌렸다는 사실도 커널에 기록한다. 기록되지 않는 상태 변경을 만들지 않기 위해서다.
        ack = self.kernel.call({
            "type": "undo",
            "request_id": request_id,
            "snapshot_path": str(snap_path),
            "snapshot_hash": restored_hash,
        })
        return {"status": "undone", "snapshot_path": str(snap_path), "ack": ack}
