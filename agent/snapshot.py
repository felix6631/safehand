"""실행 직전 state.json 스냅샷 저장/복원.

스냅샷은 커널이 아니라 orchestrator(실행 계층)가 뜬다 — 파일 I/O는 실행 계층 책임.
"""
import pathlib
import shutil
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "mocksite" / "state.json"
SNAPSHOT_DIR = ROOT / "snapshots"


def save(request_id: str, seq: int) -> pathlib.Path:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = SNAPSHOT_DIR / f"{request_id}_seq{seq}_{ts}.json"
    shutil.copy(STATE_PATH, dest)
    return dest


def restore(path: pathlib.Path) -> None:
    shutil.copy(path, STATE_PATH)
