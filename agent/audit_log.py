"""audit.jsonl을 읽어 '오늘 AI가 한 일' 요약을 만든다.

Phase 8의 접근성 UI는 이 모듈의 summarize() 결과를 그대로 큰 글씨 목록으로 렌더링한다.
판정 로직은 전혀 없다 — 이미 커널이 내린 판정(VERDICT/EXECUTED/UNDO)을 사람이 읽기 좋게
재구성할 뿐이다.
"""
import json
import pathlib
from datetime import datetime, timezone


def read_events(log_path) -> list:
    path = pathlib.Path(log_path)
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def summarize(log_path, date: str = None) -> list:
    """request_id별로 이벤트를 묶어 사람이 읽을 요약을 시간순으로 만든다."""
    events = read_events(log_path)
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    by_request = {}
    order = []
    for ev in events:
        payload = ev.get("payload", {})
        request_id = payload.get("request_id")
        if not request_id or not ev.get("ts", "").startswith(date):
            continue
        if request_id not in by_request:
            by_request[request_id] = {"request_id": request_id, "ts": ev["ts"], "events": []}
            order.append(request_id)
        by_request[request_id]["events"].append(ev)

    summaries = []
    for request_id in order:
        entry = by_request[request_id]
        evs = entry["events"]
        verdicts = [e for e in evs if e["event"] == "VERDICT"]
        executed = [e for e in evs if e["event"] == "EXECUTED"]
        undone = [e for e in evs if e["event"] == "UNDO"]

        decision = verdicts[-1]["payload"].get("decision") if verdicts else None
        user_intent = verdicts[-1]["payload"].get("user_intent") if verdicts else None
        rule_ids = sorted({
            t["rule_id"] for v in verdicts for t in v["payload"].get("triggered", [])
        })

        if undone:
            status = "되돌림"
        elif decision == "ALLOW" and executed:
            status = "정상"
        elif decision == "DENY":
            status = "차단됨"
        elif decision == "HOLD":
            status = "보류"
        else:
            status = "알 수 없음"

        summaries.append({
            "request_id": request_id,
            "ts": entry["ts"],
            "status": status,
            "user_intent": user_intent or None,
            "rule_ids": rule_ids,
            "undoable": bool(executed) and not undone,
        })

    return summaries
