"""안전 커널(C++) subprocess 래퍼.

커널은 신뢰 경계 그 자체다. 커널이 죽거나 응답하지 않으면 예외를 던져
실행을 즉시 중단시킨다 — "커널 없이 조용히 진행"하는 경로는 존재하지 않는다.
"""
import json
import subprocess
import threading


class KernelDeadError(RuntimeError):
    pass


class KernelClient:
    def __init__(self, exe_path: str, log_path: str, config_path: str, secret: str = ""):
        self.proc = subprocess.Popen(
            [exe_path, log_path, config_path, secret],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lock = threading.Lock()

    def call(self, payload: dict, timeout: float = 2.0) -> dict:
        line = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        with self.lock:
            if self.proc.poll() is not None:
                raise KernelDeadError("커널이 죽었습니다 — 안전을 위해 실행을 중단합니다")
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
            resp = self.proc.stdout.readline()
        if not resp:
            raise KernelDeadError("커널이 죽었습니다 — 안전을 위해 실행을 중단합니다")
        return json.loads(resp)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def close(self):
        if self.alive():
            self.proc.terminate()
