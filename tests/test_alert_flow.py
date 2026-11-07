"""수신 API부터 경보 저장까지의 흐름."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rafi.server.app import create_app
from rafi.server.config import Config, load_config

URL = "/api/v1/messages"
REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"


def local(hour: int, minute: int = 0) -> datetime:
    """서버 시간대의 시각. CI와 로컬의 시간대가 달라도 청소 시간대 판단이 같게 나온다."""
    return datetime(2026, 11, 7, hour, minute).astimezone()


def envelope(type_: str, payload: dict, device_id: str) -> dict:
    return {
        "schema": "1.0",
        "type": type_,
        "message_id": str(uuid4()),
        "device_id": device_id,
        "sent_at": "2026-11-07T12:00:00+09:00",
        "payload": payload,
    }


def radar_message(start: datetime, roi: str = "vent", seconds: int = 10) -> dict:
    payload = {
        "source": "radar",
        "event_start": start.isoformat(),
        "event_end": (start + timedelta(seconds=seconds)).isoformat(),
        "roi": roi,
        "score": 0.8,
        "method": "radar-v1",
        "reasons": ["천장 부근 정지"],
    }
    return envelope("radar.detection", payload, "radar-1")


def rf_window(start: datetime, bytes_up: int = 30_000) -> dict:
    stats = {
        "device": "cam-01",
        "frames_up": 30,
        "bytes_up": bytes_up,
        "frames_down": 2,
        "bytes_down": 200,
        "size_p50": 1000,
        "size_p90": 1400,
        "rssi_mean": -48,
    }
    payload = {"window_start": start.isoformat(), "window_sec": 1, "devices": [stats]}
    return envelope("rf.window", payload, "pi-1")


def send_rf_transmission(client, start: datetime, seconds: int = 15) -> None:
    for offset in range(seconds):
        assert client.post(URL, json=rf_window(start + timedelta(seconds=offset))).status_code == 200


def alerts(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as db:
        return db.execute(
            "SELECT grade, status, room, roi, in_cleaning_window FROM alerts ORDER BY alert_id"
        ).fetchall()


def detections(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT alert_id, source FROM detections ORDER BY id").fetchall()


def message_count(db_path: Path) -> int:
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "rafi.db"


@pytest.fixture
def client(db_path: Path):
    with TestClient(create_app(REPO_CONFIG, db_path)) as test_client:
        yield test_client


def test_radar_detection_creates_l1_with_room_from_config(client, db_path):
    client.post(URL, json=radar_message(local(12)))
    assert alerts(db_path) == [("L1", "unacked", "stall-1", "vent", 0)]
    assert detections(db_path) == [(1, "radar")]


def test_duplicate_detection_does_not_change_alert(client, db_path):
    message = radar_message(local(12))
    client.post(URL, json=message)
    assert client.post(URL, json=message).json() == {"status": "duplicate"}
    assert detections(db_path) == [(1, "radar")]


def test_sustained_rf_creates_l3(client, db_path):
    send_rf_transmission(client, local(12))
    assert alerts(db_path) == [("L3", "unacked", "stall-1", None, 0)]
    assert detections(db_path) == [(1, "rf")]


def test_short_rf_creates_no_alert_but_is_saved(client, db_path):
    send_rf_transmission(client, local(12), seconds=14)
    assert alerts(db_path) == []
    assert message_count(db_path) == 14


def test_duplicate_rf_window_does_not_count_toward_duration(client, db_path):
    message = rf_window(local(12))
    for _ in range(20):
        client.post(URL, json=message)
    assert alerts(db_path) == []


def test_rf_first_then_radar_promotes_to_l2(client, db_path):
    send_rf_transmission(client, local(12))
    client.post(URL, json=radar_message(local(12)))
    assert alerts(db_path) == [("L2", "unacked", "stall-1", "vent", 0)]
    assert detections(db_path) == [(1, "rf"), (1, "radar")]


def test_radar_first_then_rf_promotes_to_l2(client, db_path):
    client.post(URL, json=radar_message(local(12)))
    send_rf_transmission(client, local(12))
    assert alerts(db_path) == [("L2", "unacked", "stall-1", "vent", 0)]


def test_acked_alert_is_not_grouped(client, db_path):
    client.post(URL, json=radar_message(local(12)))
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE alerts SET status = 'acked', acked_at = '2026-11-07T12:00:30+09:00'")

    send_rf_transmission(client, local(12))

    assert [row[:2] for row in alerts(db_path)] == [("L1", "acked"), ("L3", "unacked")]


def test_open_alert_survives_restart(db_path):
    with TestClient(create_app(REPO_CONFIG, db_path)) as first:
        first.post(URL, json=radar_message(local(12)))
    with TestClient(create_app(REPO_CONFIG, db_path)) as second:
        send_rf_transmission(second, local(12))
    assert [row[0] for row in alerts(db_path)] == ["L2"]


def test_failure_during_fusion_rolls_back_message(db_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("융합 실패")

    monkeypatch.setattr("rafi.server.alerts.fuse", fail)
    app = create_app(REPO_CONFIG, db_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        message = radar_message(local(12))
        assert client.post(URL, json=message).status_code == 500

        monkeypatch.undo()  # 재전송하면 중복이 아니라 정상 처리되어야 한다
        assert client.post(URL, json=message).json() == {"status": "accepted"}

    assert message_count(db_path) == 1
    assert len(alerts(db_path)) == 1


# ---------- 청소 시간대 ----------


def test_alert_in_cleaning_window_is_marked_without_changing_grade(client, db_path):
    client.post(URL, json=radar_message(local(10, 10)))
    assert alerts(db_path) == [("L1", "unacked", "stall-1", "vent", 1)]


def test_cleaning_mark_follows_earliest_event_of_alert(client, db_path):
    client.post(URL, json=radar_message(local(10, 30)))  # 끝 시각은 청소 시간대가 아니다
    assert alerts(db_path)[0][4] == 0

    send_rf_transmission(client, local(10, 29))  # 더 이른 사건이 묶여 시작 시각이 당겨진다
    assert alerts(db_path)[0][4] == 1


@pytest.fixture
def config() -> Config:
    return load_config(REPO_CONFIG)


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(9, 59, False), (10, 0, True), (10, 29, True), (10, 30, False), (16, 15, True)],
)
def test_in_cleaning_window(config, hour, minute, expected):
    assert config.in_cleaning_window(local(hour, minute)) is expected


def write_config(tmp_path: Path, cleaning: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "rooms: {stall-1: {name: 칸 1}}\n"
        "devices: {radar-1: {kind: radar, room: stall-1}}\n"
        "device_timeout_sec: 30\n"
        "fusion_window_sec: 120\n"
        f"cleaning_windows: {cleaning}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "cleaning",
    [
        "[{start: 10:00, end: 10:30}]",  # 따옴표가 없으면 숫자로 읽힌다
        '[{start: "10:30", end: "10:00"}]',
    ],
)
def test_invalid_cleaning_window_is_rejected(tmp_path, cleaning):
    with pytest.raises(ValidationError, match="cleaning_windows"):
        load_config(write_config(tmp_path, cleaning))
