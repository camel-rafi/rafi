"""저장소: 사건(event_id) 저장과 이전 DB 이어 쓰기."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rafi.common.messages import Detection
from rafi.fusion.rules import AlertChange
from rafi.server.storage import Storage

T0 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)

# event_id(규약 8번)를 넣기 전의 detections 테이블
OLD_DETECTIONS = """
CREATE TABLE detections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    INTEGER NOT NULL REFERENCES alerts (alert_id),
    source      TEXT NOT NULL,
    event_start TEXT NOT NULL,
    event_end   TEXT NOT NULL,
    roi         TEXT,
    device      TEXT,
    rssi_mean   REAL,
    score       REAL NOT NULL,
    method      TEXT NOT NULL,
    reasons     TEXT NOT NULL
);
"""


def radar(seconds: int, event_id: str | None = None) -> Detection:
    return Detection(
        source="radar",
        event_start=T0,
        event_end=T0 + timedelta(seconds=seconds),
        roi="dispenser_top",
        score=0.8,
        method="rule-v4",
        reasons=[f"휴지 디스펜서 위 손 머묾 {seconds}.0초"],
        event_id=event_id,
    )


def save(storage: Storage, alert_id: int | None, detection: Detection) -> int:
    with storage.transaction():
        return storage.save_alert(
            AlertChange(alert_id, "L1", detection.roi, [detection]),
            "stall-1",
            detection.event_start,
            in_cleaning_window=False,
            now=detection.event_end,
        )


def ack(storage: Storage, alert_id: int) -> None:
    with storage.transaction():
        storage.ack_alert(alert_id, T0 + timedelta(minutes=1))


def test_evidence_has_event_times_and_id(tmp_path: Path):
    storage = Storage(tmp_path / "rafi.db")
    alert_id = save(storage, None, radar(3, "e1"))
    save(storage, alert_id, radar(5, "e1"))

    evidence = storage.get_alert(alert_id).evidence

    assert [e.event_id for e in evidence] == ["e1", "e1"]
    assert [e.event_end for e in evidence] == [T0 + timedelta(seconds=3), T0 + timedelta(seconds=5)]
    assert evidence[0].event_start == T0


def test_acked_alert_is_found_by_event(tmp_path: Path):
    storage = Storage(tmp_path / "rafi.db")
    alert_id = save(storage, None, radar(3, "e1"))
    assert storage.acked_alert_with_event("stall-1", "e1") is None  # 아직 미확인

    ack(storage, alert_id)

    assert storage.acked_alert_with_event("stall-1", "e1") == (alert_id, "L1")
    assert storage.acked_alert_with_event("stall-1", "e2") is None
    assert storage.acked_alert_with_event("stall-2", "e1") is None


def test_old_database_gets_event_id_column(tmp_path: Path):
    path = tmp_path / "rafi.db"
    first = Storage(path)
    first.close()
    with sqlite3.connect(path) as db:  # event_id 칸이 없던 때의 DB로 되돌리고 판단 하나를 넣어 둔다
        db.execute("DROP TABLE detections")
        db.execute(OLD_DETECTIONS)
        db.execute(
            "INSERT INTO alerts (room, grade, status, roi, started_at, updated_at, in_cleaning_window)"
            " VALUES ('stall-1', 'L1', 'unacked', 'dispenser_top', ?, ?, 0)",
            (T0.isoformat(), T0.isoformat()),
        )
        db.execute(
            "INSERT INTO detections (alert_id, source, event_start, event_end, roi, score, method, reasons)"
            " VALUES (1, 'radar', ?, ?, 'dispenser_top', 0.8, 'rule-v3', '[]')",
            (T0.isoformat(), T0.isoformat()),
        )

    storage = Storage(path)

    assert storage.get_alert(1).evidence[0].event_id is None  # 예전 판단은 그대로 읽힌다
    save(storage, 1, radar(3, "e1"))
    assert [e.event_id for e in storage.get_alert(1).evidence] == [None, "e1"]
