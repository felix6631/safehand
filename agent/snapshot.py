"""실행 직전 state.json 스냅샷 저장/복원, 그리고 되돌리기(Undo).

스냅샷은 커널이 아니라 orchestrator(실행 계층)가 뜬다 — 파일 I/O는 실행 계층 책임.
다만 스냅샷의 경로+해시는 EXECUTED 감사 이벤트로 커널에 새겨 두므로, 스냅샷 파일 자체가
사후에 조작되면 감사 로그와 대조해 탐지할 수 있다.
"""
import hashlib   # 스냅샷 파일의 지문(해시)을 만든다.
import pathlib   # 경로 계산.
import shutil    # 파일 복사(저장·복원 모두 복사 한 번으로 끝난다).
from datetime import datetime, timezone  # 파일 이름에 넣을 시각.
from typing import Optional              # '있을 수도, 없을 수도' 있는 반환값 표시.

# 이 파일 기준으로 프로젝트 루트를 찾는다. 어디서 실행하든 같은 위치를 가리키게 하려는 것이다.
ROOT = pathlib.Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "mocksite" / "state.json"  # 모의 사이트의 현재 상태 파일. 이걸 통째로 뜬다.
SNAPSHOT_DIR = ROOT / "snapshots"              # 스냅샷을 모아 두는 폴더.


def _file_hash(path: pathlib.Path) -> str:
    """스냅샷 파일의 지문. 이 값을 감사 로그에 남겨 두면 나중에 파일이 바뀌었는지 알 수 있다."""
    # 텍스트가 아니라 바이트로 읽는다. 줄바꿈 처리 차이로 해시가 달라지지 않게 하기 위해서다.
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(request_id: str, seq: int) -> dict:
    """되돌릴 수 없는 행동을 하기 '직전'의 상태를 복사해 둔다.

    파일 이름에 시각을 넣는 이유: 한 요청에 비가역 스텝이 여러 개면 각각 남겨야 하고,
    이름순으로 정렬하면 그대로 시간순이 되어 가장 최근 것을 찾기 쉽다.
    """
    SNAPSHOT_DIR.mkdir(exist_ok=True)  # 폴더가 이미 있어도 오류가 나지 않도록 exist_ok.
    # 마이크로초(%f)까지 넣는다. 스텝이 연달아 실행되면 초 단위로는 이름이 겹칠 수 있다.
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    dest = SNAPSHOT_DIR / f"{request_id}_seq{seq}_{ts}.json"
    shutil.copy(STATE_PATH, dest)  # 현재 상태를 그대로 복사해 둔다.
    # 경로와 지문을 함께 돌려준다. 둘 다 커널의 감사 로그에 기록된다.
    return {"path": str(dest), "hash": _file_hash(dest)}


def list_snapshots(request_id: str) -> list:
    """한 요청이 남긴 스냅샷을 시간순으로 모은다."""
    if not SNAPSHOT_DIR.exists():
        return []
    # 이름 앞부분이 시각 순서와 같으므로 단순 정렬로 시간순이 된다.
    return sorted(SNAPSHOT_DIR.glob(f"{request_id}_seq*_*.json"))


def latest_snapshot(request_id: str) -> Optional[pathlib.Path]:
    """가장 마지막 스냅샷. 되돌리기는 여기로 돌아간다."""
    matches = list_snapshots(request_id)
    # 정렬돼 있으므로 마지막 항목이 가장 최근이다. 하나도 없으면 None.
    return matches[-1] if matches else None


def restore(path) -> str:
    """스냅샷을 되돌려 놓고 그 지문을 반환한다.

    반환한 지문은 orchestrator가 커널에 UNDO 이벤트로 기록한다 —
    되돌리기 자체도 감사 대상이기 때문이다.
    """
    path = pathlib.Path(path)      # 문자열로 들어와도 되도록 경로 객체로 맞춘다.
    shutil.copy(path, STATE_PATH)  # 저장할 때와 반대 방향으로 복사한다.
    return _file_hash(path)
