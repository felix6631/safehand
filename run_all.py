"""모든 프로세스를 한 번에 띄우는 실행기.

  mocksite/app.py : 포트 5001 — AI가 조작할 대상(모의 은행). 안전장치가 없다.
  ui/app.py       : 포트 5000 — 사용자 화면. orchestrator를 통해 커널을 부른다.

둘은 반드시 별도 프로세스다. 그 분리가 "AI가 자기 감시자를 건드릴 수 없다"는 주장의
근거이므로 하나로 합치지 않는다. 커널은 상시 서버가 아니라 orchestrator가 세션마다
subprocess로 띄우므로 여기서는 빌드만 확인한다.
"""
import pathlib
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent
KERNEL_DIR = ROOT / "kernel"
KERNEL_EXE = KERNEL_DIR / ("safehand_kernel.exe" if sys.platform == "win32" else "safehand_kernel")


def ensure_kernel_built():
    if KERNEL_EXE.exists():
        return
    print("커널이 빌드되어 있지 않습니다. 빌드를 시작합니다...")
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "build.bat"], cwd=KERNEL_DIR, check=True)
    else:
        subprocess.run(["bash", "build.sh"], cwd=KERNEL_DIR, check=True)
    print("커널 빌드 완료:", KERNEL_EXE)


def wait_until_up(url: str, timeout: float = 20.0) -> bool:
    """mocksite가 뜨기 전에 ui를 띄우면 첫 클릭이 '연결할 수 없습니다'로 실패한다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except urllib.error.URLError:
            time.sleep(0.3)
        except OSError:
            time.sleep(0.3)
    return False


def main():
    ensure_kernel_built()

    procs = [("mocksite", subprocess.Popen([sys.executable, str(ROOT / "mocksite" / "app.py")]))]
    if not wait_until_up("http://127.0.0.1:5001/api/state_view"):
        print("모의 사이트가 시작되지 않았습니다. 5001 포트를 이미 쓰고 있는지 확인하세요.")
        procs[0][1].terminate()
        return
    print("모의 사이트 : http://127.0.0.1:5001")

    procs.append(("ui", subprocess.Popen([sys.executable, str(ROOT / "ui" / "app.py")])))
    print("사용자 화면 : http://127.0.0.1:5000   <- 여기로 접속하세요")
    print("종료하려면 Ctrl+C")

    def handle_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        while True:
            time.sleep(1)
            for name, p in procs:
                if p.poll() is not None:
                    print(f"{name} 프로세스가 종료되었습니다 (code={p.returncode})")
                    return
    except KeyboardInterrupt:
        print("\n종료 중...")
    finally:
        for _, p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
