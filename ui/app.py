"""사용자 화면 (포트 5000) — 프로세스 ①.

이 파일에는 판정 로직이 한 줄도 없다. 세 계층을 아는 유일한 지점은 orchestrator이고,
여기서는 그것을 HTTP로 감싸 화면에 연결할 뿐이다.

  브라우저 → ui/app.py → orchestrator ─┬→ planner  (프로세스 ②, 신뢰 ✗)
                                      ├→ 커널     (프로세스 ③, 신뢰 ✓)
                                      └→ executor → mocksite (프로세스 ④)

커널이 죽으면 조용히 진행하는 경로는 없다 — KernelDeadError를 그대로 화면까지 올린다.
"""
import atexit
import os
import pathlib
import secrets
import sys

import requests
from flask import Flask, jsonify, render_template, request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from agent import audit_log
from agent.executor import Executor
from agent.kernel_client import KernelClient, KernelDeadError
from agent.orchestrator import Orchestrator
from agent.planner import Planner, PlannerError

KERNEL_EXE = ROOT / "kernel" / ("safehand_kernel.exe" if sys.platform == "win32" else "safehand_kernel")
CONFIG_PATH = ROOT / "config" / "rules.json"
LOG_PATH = ROOT / "logs" / "audit.jsonl"
# 'localhost'가 아니라 127.0.0.1을 쓴다 — Windows에서 localhost는 ::1을 먼저 시도했다가
# 실패 후 IPv4로 넘어가느라 요청당 약 2초가 더 걸린다. 한 번의 실행이 수십 번 호출하므로
# 시연 체감 속도가 8초 대 0.1초로 갈린다.
MOCKSITE_URL = os.environ.get("SAFEHAND_MOCKSITE", "http://127.0.0.1:5001")

def _load_env_file() -> None:
    """프로젝트 루트의 .env를 환경변수로 올린다.
    python-dotenv가 있으면 그것을 쓰고, 없으면 KEY=VALUE 한 줄씩 직접 읽는다.
    이미 설정된 환경변수는 덮어쓰지 않는다."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_env_file()

# executor와 커널이 같은 비밀을 써야 attestation HMAC 검증(R5-a)이 성립한다.
# 프로세스마다 새로 만들므로 AI 쪽에는 존재조차 하지 않는다.
SECRET = os.environ.get("SAFEHAND_SECRET") or secrets.token_hex(16)

# API 키가 있으면 자유 입력을 실제 LLM으로 처리한다. 없으면 캐시 재생만 가능하다.
# 키가 있어도 SAFEHAND_OFFLINE=1 을 주면 캐시 재생으로 고정한다 (시연 안정성 확보용).
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
LIVE = HAS_KEY and os.environ.get("SAFEHAND_OFFLINE") != "1"

# 지능 계층이 쓸 모델. 비워 두면 Planner의 기본값을 그대로 쓴다 (여기서 중복 정의하지 않는다).
# 커널은 이 값을 알지도, 쓰지도 않는다 — 모델을 바꿔도 R1~R9는 그대로다.
MODEL = os.environ.get("SAFEHAND_MODEL") or None

# 시나리오별 사전 조건 — 캐시된 명세는 특정 화면 상태(state_hash)를 전제로 만들어졌다.
SCENARIOS = {
    "act1": {"label": "1막 · 정상 납부",     "attack": False, "instruction": "이번 달 전기요금 좀 내줘"},
    "act2": {"label": "2막 · AI가 실수",     "attack": False, "instruction": "이번 달 전기요금 좀 내줘"},
    "act3": {"label": "3막 · 인젝션 공격",   "attack": True,  "instruction": "이번 달 전기요금 좀 내줘"},
}

app = Flask(__name__)

_kernel = None
_orch = None
_planner_cached = None
_planner_live = None


class UiOrchestrator(Orchestrator):
    """HOLD 확인 화면은 '무엇을' 승인하는지 사람에게 보여줘야 하므로 마지막 명세를 보관한다.
    판정에는 전혀 관여하지 않는다 — 보관만 한다."""

    last_spec = None

    def run(self, spec: dict, user_instruction: str = None) -> dict:
        self.last_spec = spec
        return super().run(spec, user_instruction=user_instruction)


def get_orch() -> UiOrchestrator:
    """커널을 한 번만 띄우고 재사용한다. 죽어 있으면 새로 띄운다."""
    global _kernel, _orch
    if _kernel is not None and not _kernel.alive():
        _kernel, _orch = None, None
    if _orch is None:
        if not KERNEL_EXE.exists():
            raise FileNotFoundError(f"커널이 빌드되어 있지 않습니다: {KERNEL_EXE}")
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _kernel = KernelClient(str(KERNEL_EXE), str(LOG_PATH), str(CONFIG_PATH), SECRET)
        _orch = UiOrchestrator(_kernel, Executor(MOCKSITE_URL, secret=SECRET))
    return _orch


def get_planner(scenario: str = None) -> Planner:
    """1~3막 시나리오는 키가 있어도 항상 캐시를 재생한다 — 심사 시연이 네트워크나
    모델의 그날 컨디션에 흔들리면 안 되기 때문이다. 자유 입력만 실제 LLM을 부른다."""
    global _planner_cached, _planner_live
    if scenario or not LIVE:
        if _planner_cached is None:
            _planner_cached = Planner(offline=True)
        return _planner_cached
    if _planner_live is None:
        _planner_live = Planner(model=MODEL) if MODEL else Planner()
    return _planner_live


@atexit.register
def _shutdown():
    if _kernel is not None:
        _kernel.close()


# ------------------------------------------------------------ 명세 → 사람 말

def humanize(spec: dict) -> dict:
    """행동 명세를 사람이 확인할 수 있는 형태로 바꾼다. 값은 그대로 옮기기만 한다."""
    if not spec:
        return {}
    amount, payee = None, None
    for st in spec.get("steps", []):
        if st.get("target") in ("amount", "transfer_amount"):
            amount = st.get("value")
        if st.get("target") in ("payee", "recipient", "account_no"):
            payee = st.get("value")
    return {
        "request_id": spec.get("request_id"),
        "intent": spec.get("user_intent"),
        "confidence": spec.get("model_confidence"),
        "amount": amount,
        "payee": payee,
        "steps": [
            {"seq": s.get("seq"), "action": s.get("action"), "target": s.get("target"),
             "value": s.get("value"), "irreversible": bool(s.get("irreversible"))}
            for s in spec.get("steps", [])
        ],
    }


def balance_now():
    try:
        return get_orch().executor.get_state_view().get("balance")
    except Exception:
        return None


def envelope(result: dict, balance_before=None) -> dict:
    """orchestrator 결과에 화면이 필요로 하는 부수 정보를 덧붙인다.

    balance_before를 함께 돌려주는 이유: 화면은 'AI가 무엇을 하려 했는지'가 아니라
    '실제로 무엇이 바뀌었는지'를 보여줘야 한다. 실행이 끝났다는 사실과 돈이 오갔다는
    사실은 전혀 다른 이야기다."""
    orch = get_orch()
    return {
        "ok": True,
        "balance_before": balance_before,
        "status": result.get("status"),
        "verdict": result.get("verdict"),
        "resolve": result.get("resolve"),
        "step_check": result.get("step_check"),
        "results": result.get("results"),
        "plan": humanize(orch.last_spec),
        "balance": balance_now(),
    }


def fail(message: str, code: int = 500):
    return jsonify({"ok": False, "error": message}), code


# ----------------------------------------------------------------- 화면 라우트

@app.route("/")
def index():
    return render_template("index.html", scenarios=SCENARIOS, live=LIVE)


# ------------------------------------------------------------------- 공개 API

@app.route("/api/status")
def api_status():
    try:
        orch = get_orch()
    except FileNotFoundError as e:
        return fail(str(e), 503)
    try:
        sv = orch.executor.get_state_view()
    except requests.exceptions.RequestException:
        return fail(f"모의 사이트에 연결할 수 없습니다 ({MOCKSITE_URL}). mocksite/app.py를 먼저 실행하세요.", 503)
    return jsonify({
        "ok": True, "kernel_alive": orch.kernel.alive(),
        "balance": sv.get("balance"), "page": sv.get("page"),
        "mode": f"실시간 LLM ({get_planner().model})" if LIVE else "오프라인 재생",
        "live": LIVE,
    })


@app.route("/api/prepare", methods=["POST"])
def api_prepare():
    """시나리오 사전 조건을 맞춘다 — 캐시된 명세는 특정 화면 상태를 전제로 한다."""
    scenario = (request.get_json(silent=True) or {}).get("scenario")
    cfg = SCENARIOS.get(scenario, {"attack": False})
    try:
        requests.post(f"{MOCKSITE_URL}/api/reset", timeout=5).raise_for_status()
        requests.post(f"{MOCKSITE_URL}/api/attack", json={"on": cfg["attack"]}, timeout=5).raise_for_status()
        requests.post(f"{MOCKSITE_URL}/api/act",
                      json={"action": "navigate", "target": "/bills"}, timeout=5).raise_for_status()
    except requests.exceptions.RequestException as e:
        return fail(f"모의 사이트 준비에 실패했습니다: {e}", 503)
    return jsonify({"ok": True, "balance": balance_now(), "attack": cfg["attack"]})


@app.route("/api/run", methods=["POST"])
def api_run():
    body = request.get_json(silent=True) or {}
    scenario = body.get("scenario")
    instruction = body.get("instruction") or SCENARIOS.get(scenario, {}).get("instruction", "")
    if not instruction.strip():
        return fail("지시 내용을 입력해 주세요.", 400)

    before = balance_now()
    try:
        result = get_orch().plan_and_run(instruction, get_planner(scenario), scenario=scenario)
    except PlannerError as e:
        if not scenario and not LIVE:
            return fail(
                "자유 입력을 쓰려면 API 키가 필요합니다. 프로젝트 루트 .env에 "
                "ANTHROPIC_API_KEY를 넣고 ui/app.py를 다시 실행하세요. "
                "키 없이도 아래 1~3막 시연 버튼은 그대로 동작합니다.", 400)
        return fail(f"AI가 계획을 만들지 못했습니다 — {e}", 400)
    except KernelDeadError as e:
        return fail(str(e), 503)
    except requests.exceptions.RequestException:
        return fail(f"모의 사이트에 연결할 수 없습니다 ({MOCKSITE_URL}).", 503)
    except Exception as e:
        # LLM 호출 실패(잘못된 키·한도 초과·네트워크 등)를 500 크래시 대신 읽을 수 있는
        # 메시지로 돌려준다. 커널 판정과는 무관한, 지능 계층 쪽 실패다.
        return fail(f"AI 호출에 실패했습니다 — {type(e).__name__}: {e}", 502)
    return jsonify(envelope(result, before))


@app.route("/api/resolve", methods=["POST"])
def api_resolve():
    body = request.get_json(silent=True) or {}
    before = balance_now()
    try:
        result = get_orch().resolve(
            body.get("request_id"), body.get("challenge"), approve=bool(body.get("approve")))
    except KernelDeadError as e:
        return fail(str(e), 503)
    except Exception as e:
        return fail(f"실행에 실패했습니다 — {type(e).__name__}: {e}", 502)
    return jsonify(envelope(result, before))


@app.route("/api/undo", methods=["POST"])
def api_undo():
    body = request.get_json(silent=True) or {}
    try:
        result = get_orch().undo(body.get("request_id"))
    except KernelDeadError as e:
        return fail(str(e), 503)
    return jsonify({"ok": True, **result, "balance": balance_now()})


@app.route("/api/audit")
def api_audit():
    return jsonify({"ok": True, "items": audit_log.summarize(LOG_PATH)})


if __name__ == "__main__":
    print(f"safehand ui : http://127.0.0.1:5000   (모드: {'실시간 LLM' if LIVE else '오프라인 재생'})")
    print(f"모의 사이트  : {MOCKSITE_URL}  — 먼저 실행되어 있어야 합니다")
    if not HAS_KEY:
        print("ANTHROPIC_API_KEY가 없어 자유 입력은 막혀 있습니다 (.env에 넣으면 켜집니다).")
        print("1~3막 시연 버튼은 키 없이도 그대로 동작합니다.")
    app.run(port=5000, debug=False, use_reloader=False)
