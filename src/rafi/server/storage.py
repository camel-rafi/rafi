"""SQLite 저장소. 서버의 이벤트 루프에서만 사용하므로 별도 잠금을 두지 않는다."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from rafi.common.messages import SensorMessage

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    message_id  TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    device_id   TEXT NOT NULL,
    sent_at     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    raw_json    TEXT NOT NULL
)
"""


class Storage:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(SCHEMA)
        self._db.commit()

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
        self._db.commit()
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

    def close(self) -> None:
        self._db.close()
