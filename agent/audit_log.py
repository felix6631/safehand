"""audit.jsonl을 읽어 '오늘 AI가 한 일' 요약을 만든다.

Phase 8의 접근성 UI는 이 모듈의 summarize() 결과를 그대로 큰 글씨 목록으로 렌더링한다.
판정 로직은 전혀 없다 — 이미 커널이 내린 판정(VERDICT/EXECUTED/UNDO)을 사람이 읽기 좋게
재구성할 뿐이다.
"""
import json      # 로그 한 줄이 JSON이다.
import pathlib   # 로그 파일 경로 처리.
from datetime import datetime, timezone  # '오늘'이 며칠인지 계산.


def read_events(log_path) -> list:
    """감사 로그를 한 줄씩 읽는다. 한 줄이 이벤트 하나다.

    깨진 줄은 건너뛴다. 읽기 도중 예외로 죽으면 '오늘 AI가 한 일'을 아예 못 보여주는데,
    그건 로그를 남기는 목적에 어긋나기 때문이다. (변조 탐지는 커널의 해시 체인 몫이다.)
    """
    path = pathlib.Path(log_path)
    # 아직 한 번도 실행한 적이 없으면 파일이 없다. 오류가 아니라 '기록 없음'이다.
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:      # 빈 줄은 건너뛴다(파일 끝 줄바꿈 등).
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue      # 깨진 줄 하나 때문에 전체를 못 보여주면 안 된다.
    return events


def summarize(log_path, date: str = None) -> list:
    """request_id별로 이벤트를 묶어 사람이 읽을 요약을 시간순으로 만든다.

    한 요청은 보통 여러 줄로 남는다 (VERDICT -> EXECUTED -> UNDO ...).
    사용자에게는 그걸 "무엇을 했고 어떻게 됐는지" 한 줄로 보여줘야 한다.
    """
    events = read_events(log_path)
    # date를 안 주면 오늘로 본다. 로그의 ts가 UTC라 여기서도 UTC로 맞춘다.
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    by_request = {}  # request_id -> 그 요청에 속한 이벤트 묶음
    order = []       # 처음 등장한 순서. dict만으로는 '시간순'을 보장하기 애매해 따로 둔다.
    for ev in events:
        payload = ev.get("payload", {})
        request_id = payload.get("request_id")
        # request_id가 없는 이벤트나 오늘이 아닌 기록은 건너뛴다.
        if not request_id or not ev.get("ts", "").startswith(date):
            continue
        if request_id not in by_request:
            # 처음 본 요청이면 자리를 만든다. ts는 첫 이벤트 시각으로 고정한다.
            by_request[request_id] = {"request_id": request_id, "ts": ev["ts"], "events": []}
            order.append(request_id)
        by_request[request_id]["events"].append(ev)

    summaries = []
    for request_id in order:
        entry = by_request[request_id]
        evs = entry["events"]
        # 한 요청 안의 이벤트를 종류별로 나눈다. 이 세 가지로 최종 상태가 정해진다.
        verdicts = [e for e in evs if e["event"] == "VERDICT"]   # 커널의 판정
        executed = [e for e in evs if e["event"] == "EXECUTED"]  # 실제 실행된 스텝
        undone = [e for e in evs if e["event"] == "UNDO"]        # 되돌리기

        # HOLD 뒤 승인되면 VERDICT가 두 번 남는다. 최종 상태는 마지막 판정이 결정한다.
        decision = verdicts[-1]["payload"].get("decision") if verdicts else None
        user_intent = verdicts[-1]["payload"].get("user_intent") if verdicts else None
        # 발동한 규칙을 중복 없이 모아 정렬한다(집합 -> 정렬). 화면에 "R2, R5"처럼 보여준다.
        rule_ids = sorted({
            t["rule_id"] for v in verdicts for t in v["payload"].get("triggered", [])
        })

        # 순서가 중요하다. 되돌렸으면 '실행됨'이 아니라 '되돌림'으로 보여야 한다.
        if undone:
            status = "되돌림"
        elif decision == "ALLOW" and executed:
            # ALLOW만으로는 부족하다. 실제로 실행된 기록이 있어야 '정상'이다.
            status = "정상"
        elif decision == "DENY":
            status = "차단됨"
        elif decision == "HOLD":
            status = "보류"   # 사용자가 아직 승인/취소하지 않은 상태.
        else:
            status = "알 수 없음"

        summaries.append({
            "request_id": request_id,
            "ts": entry["ts"],
            "status": status,
            "user_intent": user_intent or None,
            "rule_ids": rule_ids,
            # 되돌리기 버튼을 보여줄지 판단하는 값. 실행된 적이 있고 아직 안 되돌렸을 때만 True.
            "undoable": bool(executed) and not undone,
        })

    return summaries
