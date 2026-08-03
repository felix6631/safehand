"""실행 직전 state.json 스냅샷 저장/복원, 그리고 되돌리기(Undo).

스냅샷은 커널이 아니라 orchestrator(실행 계층)가 뜬다 — 파일 I/O는 실행 계층 책임.
다만 스냅샷의 경로+해시는 EXECUTED 감사 이벤트로 커널에 새겨 두므로, 스냅샷 파일 자체가
사후에 조작되면 감사 로그와 대조해 탐지할 수 있다.
"""
import hashlib
import pathlib
import shutil
from datetime import datetime, timezone
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "mocksite" / "state.json"
SNAPSHOT_DIR = ROOT / "snapshots"


def _file_hash(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(request_id: str, seq: int) -> dict:
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = SNAPSHOT_DIR / f"{request_id}_seq{seq}_{ts}.json"
    shutil.copy(STATE_PATH, dest)
    return {"path": str(dest), "hash": _file_hash(dest)}


def list_snapshots(request_id: str) -> list:
    if not SNAPSHOT_DIR.exists():
        return []
    return sorted(SNAPSHOT_DIR.glob(f"{request_id}_seq*_*.json"))


def latest_snapshot(request_id: str) -> Optional[pathlib.Path]:
    matches = list_snapshots(request_id)
    return matches[-1] if matches else None


def restore(path) -> str:
    path = pathlib.Path(path)
    shutil.copy(path, STATE_PATH)
    return _file_hash(path)
