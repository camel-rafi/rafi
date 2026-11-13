"""SQLite 저장소. 서버의 이벤트 루프에서만 사용하므로 별도 잠금을 두지 않는다."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from rafi.common.messages import Alert, AlertStatus, Detection, Evidence, SensorMessage
from rafi.fusion.rules import AlertChange, OpenAlert

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    raw_json    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    room               TEXT NOT NULL,
    grade              TEXT NOT NULL,
    status             TEXT NOT NULL,
    roi                TEXT,
    started_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    acked_at           TEXT,
    in_cleaning_window INTEGER NOT NULL
);

-- RF 판단 하나가 경보 여러 개에 연결되면 경보마다 한 행씩 저장한다
CREATE TABLE IF NOT EXISTS detections (
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

ALERT_SELECT = """
SELECT alert_id, grade, status, room, roi, started_at, updated_at, acked_at, in_cleaning_window
FROM alerts
"""


class Storage:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def transaction(self) -> sqlite3.Connection:
        """`with storage.transaction():` 안의 변경을 모두 반영하거나, 오류 시 모두 취소한다."""
        return self._db

    def save_message(self, message: SensorMessage, raw_json: str) -> bool:
        """원본 메시지를 저장한다. 이미 받은 message_id면 저장하지 않고 False를 돌려준다."""
        cursor = self._db.execute(
            """
            INSERT INTO messages (message_id, type, device_id, sent_at, received_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (message_id) DO NOTHING
            """,
            (
                str(message.message_id),
                message.type,
                message.device_id,
                message.sent_at.isoformat(),
                datetime.now().astimezone().isoformat(),
                raw_json,
            ),
        )
        return cursor.rowcount == 1

    def list_messages(self, limit: int) -> list[dict]:
        """최근에 저장된 메시지부터 돌려준다."""
        rows = self._db.execute(
            """
            SELECT received_at, raw_json FROM messages
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {"received_at": received_at, "message": json.loads(raw_json)}
            for received_at, raw_json in rows
        ]

    def open_alerts(self, room: str) -> list[OpenAlert]:
        """칸의 미확인 경보를 연결된 판단과 함께 돌려준다."""
        rows = self._db.execute(
            """
            SELECT alert_id, grade, roi FROM alerts
            WHERE room = ? AND status = 'unacked'
            ORDER BY alert_id
            """,
            (room,),
        ).fetchall()
        return [
            OpenAlert(alert_id, grade, roi, self._detections_of(alert_id))
            for alert_id, grade, roi in rows
        ]

    def save_alert(
        self,
        change: AlertChange,
        room: str,
        started_at: datetime,
        in_cleaning_window: bool,
        now: datetime,
    ) -> int:
        """융합 결과를 저장하고 경보 번호를 돌려준다."""
        alert_id = change.alert_id
        if alert_id is None:
            cursor = self._db.execute(
                """
                INSERT INTO alerts
                    (room, grade, status, roi, started_at, updated_at, in_cleaning_window)
                VALUES (?, ?, 'unacked', ?, ?, ?, ?)
                """,
                (
                    room,
                    change.grade,
                    change.roi,
                    started_at.isoformat(),
                    now.isoformat(),
                    in_cleaning_window,
                ),
            )
            alert_id = cursor.lastrowid
        else:
            self._db.execute(
                """
                UPDATE alerts
                SET grade = ?, roi = ?, started_at = ?, updated_at = ?, in_cleaning_window = ?
                WHERE alert_id = ?
                """,
                (
                    change.grade,
                    change.roi,
                    started_at.isoformat(),
                    now.isoformat(),
                    in_cleaning_window,
                    alert_id,
                ),
            )

        self._db.executemany(
            """
            INSERT INTO detections
                (alert_id, source, event_start, event_end, roi, device,
                 rssi_mean, score, method, reasons)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    alert_id,
                    detection.source,
                    detection.event_start.isoformat(),
                    detection.event_end.isoformat(),
                    detection.roi,
                    detection.device,
                    detection.rssi_mean,
                    detection.score,
                    detection.method,
                    json.dumps(detection.reasons, ensure_ascii=False),
                )
                for detection in change.detections
            ],
        )
        return alert_id

    def get_alert(self, alert_id: int) -> Alert | None:
        row = self._db.execute(f"{ALERT_SELECT} WHERE alert_id = ?", (alert_id,)).fetchone()
        return self._to_alert(row) if row else None

    def list_alerts(self, limit: int, status: AlertStatus | None = None) -> list[Alert]:
        """최근에 만들어진 경보부터 돌려준다."""
        rows = self._db.execute(
            f"""
            {ALERT_SELECT}
            WHERE ? IS NULL OR status = ?
            ORDER BY alert_id DESC
            LIMIT ?
            """,
            (status, status, limit),
        ).fetchall()
        return [self._to_alert(row) for row in rows]

    def ack_alert(self, alert_id: int, now: datetime) -> bool:
        """경보를 확인 처리한다. 미확인 경보가 아니면 아무것도 바꾸지 않고 False를 돌려준다."""
        cursor = self._db.execute(
            """
            UPDATE alerts
            SET status = 'acked', acked_at = ?, updated_at = ?
            WHERE alert_id = ? AND status = 'unacked'
            """,
            (now.isoformat(), now.isoformat(), alert_id),
        )
        return cursor.rowcount == 1

    def _to_alert(self, row: tuple) -> Alert:
        alert_id, grade, status, room, roi, started_at, updated_at, acked_at, in_cleaning = row
        return Alert(
            alert_id=alert_id,
            grade=grade,
            status=status,
            room=room,
            roi=roi,
            started_at=started_at,
            updated_at=updated_at,
            acked_at=acked_at,
            in_cleaning_window=bool(in_cleaning),
            evidence=[
                Evidence(
                    source=detection.source,
                    score=detection.score,
                    method=detection.method,
                    reasons=detection.reasons,
                )
                for detection in self._detections_of(alert_id)
            ],
        )

    def _detections_of(self, alert_id: int) -> list[Detection]:
        rows = self._db.execute(
            """
            SELECT source, event_start, event_end, roi, device,
                   rssi_mean, score, method, reasons
            FROM detections
            WHERE alert_id = ?
            ORDER BY id
            """,
            (alert_id,),
        )
        return [
            Detection(
                source=source,
                event_start=event_start,
                event_end=event_end,
                roi=roi,
                device=device,
                rssi_mean=rssi_mean,
                score=score,
                method=method,
                reasons=json.loads(reasons),
            )
            for source, event_start, event_end, roi, device, rssi_mean, score, method, reasons
            in rows
        ]

    def close(self) -> None:
        self._db.close()
