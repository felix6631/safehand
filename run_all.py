"""모든 프로세스를 한 번에 띄우는 실행기.

  mocksite/app.py : 포트 5001 — AI가 조작할 대상(모의 은행). 안전장치가 없다.
  ui/app.py       : 포트 5000 — 사용자 화면. orchestrator를 통해 커널을 부른다.

둘은 반드시 별도 프로세스다. 그 분리가 "AI가 자기 감시자를 건드릴 수 없다"는 주장의
근거이므로 하나로 합치지 않는다. 커널은 상시 서버가 아니라 orchestrator가 세션마다
subprocess로 띄우므로 여기서는 빌드만 확인한다.

사용법:
  python run_all.py            서버만 띄운다 (수동으로 브라우저 접속)
  python run_all.py --demo     서버를 띄우고 상태를 초기화한 뒤 브라우저를 자동으로 연다
  python run_all.py --replay N 브라우저 없이 터미널에서 N막(1|2|3)을 곧장 재생한다
                                — 시연 중 브라우저가 말썽일 때의 최후 수단
"""
import argparse
import pathlib
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
KERNEL_DIR = ROOT / "kernel"
KERNEL_EXE = KERNEL_DIR / ("safehand_kernel.exe" if sys.platform == "win32" else "safehand_kernel")
MOCKSITE_URL = "http://127.0.0.1:5001"
UI_URL = "http://127.0.0.1:5000"


def ensure_kernel_built():
    if KERNEL_EXE.exists():
        return
    print("커널이 빌드되어 있지 않습니다. 빌드를 시작합니다...")
    # 절대 경로를 넘긴다 — cwd만 넘기면 일부 환경(Git Bash에서 띄운 Python 등)의
    # cmd.exe가 상대 경로의 배치 파일을 못 찾는다("build.bat은 내부 또는 외부 명령이 아닙니다").
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", str(KERNEL_DIR / "build.bat")], cwd=str(KERNEL_DIR), check=True)
    else:
        subprocess.run(["bash", str(KERNEL_DIR / "build.sh")], cwd=str(KERNEL_DIR), check=True)
    print("커널 빌드 완료:", KERNEL_EXE)


def is_up(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=1)
        return True
    except (urllib.error.URLError, OSError):
        return False


def wait_until_up(url: str, timeout: float = 20.0) -> bool:
    """mocksite가 뜨기 전에 ui를 띄우면 첫 클릭이 '연결할 수 없습니다'로 실패한다."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_up(url):
            return True
        time.sleep(0.3)
    return False


def run_servers(open_browser: bool) -> None:
    ensure_kernel_built()

    procs = [("mocksite", subprocess.Popen([sys.executable, str(ROOT / "mocksite" / "app.py")]))]
    if not wait_until_up(f"{MOCKSITE_URL}/api/state_view"):
        print("모의 사이트가 시작되지 않았습니다. 5001 포트를 이미 쓰고 있는지 확인하세요.")
        procs[0][1].terminate()
        return
    print(f"모의 사이트 : {MOCKSITE_URL}")

    if open_browser:
        import requests
        requests.post(f"{MOCKSITE_URL}/api/reset", timeout=5)

    procs.append(("ui", subprocess.Popen([sys.executable, str(ROOT / "ui" / "app.py")])))
    if not wait_until_up(f"{UI_URL}/api/status"):
        print("사용자 화면이 시작되지 않았습니다. 5000 포트를 이미 쓰고 있는지 확인하세요.")
        for _, p in procs:
            p.terminate()
        return
    print(f"사용자 화면 : {UI_URL}   <- 여기로 접속하세요")
    print("종료하려면 Ctrl+C")

    if open_browser:
        webbrowser.open(UI_URL)

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


def replay_act(n: int) -> None:
    """브라우저 없이 터미널에서 곧장 1~3막을 재생한다. 오프라인 캐시만 쓰므로
    네트워크나 API 키가 필요 없다 — 시연 중 브라우저가 실패해도 이것만은 동작해야 한다."""
    import requests

    from agent.executor import Executor
    from agent.kernel_client import KernelClient, KernelDeadError
    from agent.orchestrator import Orchestrator
    from agent.planner import Planner
    from ui.app import SCENARIOS

    scenario = f"act{n}"
    cfg = SCENARIOS[scenario]
    print(f"=== {cfg['label']} — 오프라인 재생 ===")

    ensure_kernel_built()

    started_mocksite = None
    if not is_up(f"{MOCKSITE_URL}/api/state_view"):
        print("모의 사이트가 떠 있지 않아 새로 띄웁니다...")
        started_mocksite = subprocess.Popen(
            [sys.executable, str(ROOT / "mocksite" / "app.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_until_up(f"{MOCKSITE_URL}/api/state_view"):
            print("모의 사이트를 띄우지 못했습니다.")
            started_mocksite.terminate()
            return

    requests.post(f"{MOCKSITE_URL}/api/reset", timeout=5)
    requests.post(f"{MOCKSITE_URL}/api/attack", json={"on": cfg["attack"]}, timeout=5)
    requests.post(f"{MOCKSITE_URL}/api/act", json={"action": "navigate", "target": "/bills"}, timeout=5)

    secret = secrets.token_hex(16)
    kernel = KernelClient(str(KERNEL_EXE), str(ROOT / "logs" / "audit.jsonl"),
                           str(ROOT / "config" / "rules.json"), secret)
    executor = Executor(MOCKSITE_URL, secret=secret)
    orch = Orchestrator(kernel, executor)
    planner = Planner(offline=True)

    try:
        result = orch.plan_and_run(cfg["instruction"], planner, scenario=scenario)
        _print_result(result)

        if result["status"] == "hold":
            request_id = result["verdict"]["request_id"]
            answer = input("이대로 진행할까요? [y/N] ").strip().lower()
            if answer == "y":
                result = orch.resolve(request_id, result["verdict"]["challenge"], approve=True)
                print("\n--- 승인 후 ---")
                _print_result(result)
    except KernelDeadError as e:
        print(f"커널이 죽었습니다: {e}")
    finally:
        kernel.close()
        if started_mocksite is not None:
            started_mocksite.terminate()


def _print_result(result: dict) -> None:
    status = result.get("status")
    verdict = result.get("verdict") or result.get("resolve") or {}
    print(f"판정: {status} / {verdict.get('decision')}")
    for t in verdict.get("triggered", []):
        print(f"  [{t['rule_id']}] {t['message_ko']} ({t.get('detail', '')})")
    if status == "executed":
        print("실행됨 — 돈이 실제로 움직였을 수 있습니다. 되돌리려면 사용자 화면의 '되돌리기'를 쓰세요.")


def main():
    parser = argparse.ArgumentParser(description="세이프핸드 실행기")
    parser.add_argument("--demo", action="store_true", help="상태를 초기화하고 브라우저를 자동으로 연다")
    parser.add_argument("--replay", type=int, choices=[1, 2, 3], metavar="N",
                         help="브라우저 없이 터미널에서 N막을 곧장 재생한다")
    args = parser.parse_args()

    if args.replay:
        replay_act(args.replay)
    else:
        run_servers(open_browser=args.demo)


if __name__ == "__main__":
    main()
