"""안전 커널(C++) subprocess 래퍼.

커널은 신뢰 경계 그 자체다. 커널이 죽거나 응답하지 않으면 예외를 던져
실행을 즉시 중단시킨다 — "커널 없이 조용히 진행"하는 경로는 존재하지 않는다.
"""
import json         # 커널과 주고받는 형식이 JSON 한 줄이다.
import subprocess   # 커널을 별도 프로세스로 띄우기 위해 사용.
import threading    # 동시 호출이 섞이지 않도록 잠금(lock)에 사용.


class KernelDeadError(RuntimeError):
    """커널이 죽었거나 응답이 없을 때 던진다.

    이 예외를 잡아서 '그냥 진행'하면 안 된다. 그러면 안전 계층이 없는 것과 같다.
    """
    pass


class KernelClient:
    """커널을 자식 프로세스로 띄우고 JSON 한 줄씩 주고받는다.

    무거운 연동(gRPC 등) 대신 표준 입출력을 쓰는 이유는 설치할 것을 없애기 위해서이기도 하지만,
    커널이 '별도 실행 파일'이라는 사실 자체가 이 프로젝트의 주장이기 때문이다.
    커널 프로세스에는 LLM 라이브러리가 닿지 않는다.
    """

    def __init__(self, exe_path: str, log_path: str, config_path: str, secret: str = ""):
        # secret은 명령줄 인자로 넘긴다. 이 프로세스(orchestrator)와 커널만 아는 값이며
        # planner를 띄우는 쪽에는 전달하지 않는다.
        self.proc = subprocess.Popen(
            # 커널이 받는 인자 순서: 실행파일, 감사로그 경로, 설정 경로, 비밀키.
            [exe_path, log_path, config_path, secret],
            stdin=subprocess.PIPE,      # 파이썬 -> 커널로 요청을 쓰는 통로.
            stdout=subprocess.PIPE,     # 커널 -> 파이썬으로 판정을 받는 통로.
            stderr=subprocess.DEVNULL,  # 커널의 오류 출력은 버린다. 응답 통로와 섞이면 파싱이 깨진다.
            text=True,                  # 바이트가 아니라 문자열로 주고받는다.
            encoding="utf-8",           # 한글 메시지가 깨지지 않도록 인코딩을 명시.
            bufsize=1,                  # 줄 단위 버퍼링. 한 줄 쓰면 곧바로 전달되어야 응답이 온다.
        )
        # 스텝을 여러 개 실행할 때 호출이 겹칠 수 있어 잠금을 둔다.
        self.lock = threading.Lock()

    def call(self, payload: dict, timeout: float = 2.0) -> dict:
        """커널에 한 줄 보내고 한 줄 받는다.

        커널이 죽어 있으면 예외를 던진다. '커널 없이 조용히 진행'하는 경로는 만들지 않는다 —
        판정을 못 받았는데 실행이 이어지면 안전 계층이 없는 것과 같다.
        """
        # 요청도 한 줄 JSON이어야 한다. 줄바꿈이 섞이면 커널의 getline 루프가 깨진다.
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        # 여러 스텝이 동시에 커널을 부르면 요청/응답 짝이 뒤섞이므로 한 번에 하나씩만 보낸다.
        with self.lock:
            # poll()이 None이 아니면 프로세스가 이미 끝났다는 뜻이다. 보내기 전에 먼저 확인한다.
            if self.proc.poll() is not None:
                raise KernelDeadError("커널이 죽었습니다 — 안전을 위해 실행을 중단합니다")
            self.proc.stdin.write(line)   # 요청 한 줄을 보낸다.
            self.proc.stdin.flush()       # 버퍼에 남아 있으면 커널이 못 받으므로 즉시 밀어낸다.
            resp = self.proc.stdout.readline()  # 판정 한 줄이 올 때까지 기다린다.
        # 빈 응답 = 커널이 중간에 종료됨. 이것도 실패로 본다.
        if not resp:
            raise KernelDeadError("커널이 죽었습니다 — 안전을 위해 실행을 중단합니다")
        return json.loads(resp)

    def alive(self) -> bool:
        """커널이 아직 살아 있는지 확인한다 (화면의 '커널 정상' 표시에 사용)."""
        # poll()은 아직 실행 중이면 None, 끝났으면 종료 코드를 돌려준다.
        return self.proc.poll() is None

    def close(self):
        """커널 프로세스를 정리한다. 프로그램을 끝낼 때 호출한다."""
        # 이미 죽은 프로세스에 terminate()를 부르지 않도록 먼저 확인한다.
        if self.alive():
            self.proc.terminate()
