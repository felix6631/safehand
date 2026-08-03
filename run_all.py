"""모든 프로세스를 한 번에 띄우는 실행기.

현재는 mocksite만 서버로 뜬다. ui는 Phase 8에서 추가된다.
커널은 상시 서버가 아니라 orchestrator가 세션마다 subprocess로 띄우는 구조라 여기서는
빌드만 확인한다.
"""
import pathlib
import signal
import subprocess
import sys
import time

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


def main():
    ensure_kernel_built()

    procs = [("mocksite", subprocess.Popen([sys.executable, str(ROOT / "mocksite" / "app.py")]))]
    print("mocksite: http://localhost:5001")
    print("(ui는 아직 없습니다 — Phase 8에서 추가됩니다)")

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
