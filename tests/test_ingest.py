import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rafi.server.app import create_app
from rafi.server.config import load_config

URL = "/api/v1/messages"
JSON_HEADER = {"Content-Type": "application/json"}
REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"

RADAR_STATUS = {"sensor_ok": True, "fps": 20.1, "error": None}
RF_STATUS = {"sensor_ok": True, "channel": 6, "dropped_frames": 0, "error": None}


def message(type_: str = "radar.status", payload: dict = RADAR_STATUS, **overrides) -> dict:
    return {
        "schema": "1.0",
        "type": type_,
        "message_id": str(uuid4()),
        "device_id": "radar-1",
        "sent_at": "2026-10-20T14:03:11.200+09:00",
        "payload": payload,
        **overrides,
    }


def saved_rows(db_path: Path) -> list[tuple]:
    with sqlite3.connect(db_path) as db:
        return db.execute("SELECT message_id, type, device_id, raw_json FROM messages").fetchall()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "rafi.db"


@pytest.fixture
def client(db_path: Path):
    with TestClient(create_app(REPO_CONFIG, db_path)) as test_client:
        yield test_client


def test_valid_message_is_saved_with_original_body(client, db_path):
    msg = message()
    body = json.dumps(msg, ensure_ascii=False)

    response = client.post(URL, content=body, headers=JSON_HEADER)

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert saved_rows(db_path) == [(msg["message_id"], "radar.status", "radar-1", body)]


def test_duplicate_message_is_saved_once(client, db_path):
    msg = message()

    assert client.post(URL, json=msg).json() == {"status": "accepted"}
    response = client.post(URL, json=msg)

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}
    assert len(saved_rows(db_path)) == 1


def test_duplicate_is_detected_after_restart(db_path):
    msg = message()
    with TestClient(create_app(REPO_CONFIG, db_path)) as first:
        assert first.post(URL, json=msg).json() == {"status": "accepted"}
    with TestClient(create_app(REPO_CONFIG, db_path)) as second:
        assert second.post(URL, json=msg).json() == {"status": "duplicate"}


def test_invalid_message_is_rejected_with_error_location(client, db_path):
    response = client.post(URL, json=message(payload={**RADAR_STATUS, "fsp": 20}))

    assert response.status_code == 422
    assert any("fsp" in error["loc"] for error in response.json()["detail"])
    assert saved_rows(db_path) == []


def test_detection_rule_violation_returns_serializable_error(client, db_path):
    payload = {
        "source": "radar",
        "event_start": "2026-10-20T14:02:51+09:00",
        "event_end": "2026-10-20T14:02:50+09:00",
        "roi": "vent",
        "score": 0.8,
        "method": "rule-v1",
        "reasons": [],
    }
    response = client.post(URL, json=message("radar.detection", payload))

    assert response.status_code == 422
    assert "event_end" in response.json()["detail"][0]["msg"]
    assert saved_rows(db_path) == []


@pytest.mark.parametrize("headers", [JSON_HEADER, {"Content-Type": "text/plain"}, {}])
@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe", b"[]", b""])
def test_malformed_body_is_rejected(client, db_path, body, headers):
    assert client.post(URL, content=body, headers=headers).status_code == 422
    assert saved_rows(db_path) == []


def test_valid_body_without_json_content_type_is_rejected(client, db_path):
    response = client.post(URL, content=json.dumps(message()))
    assert response.status_code == 422
    assert saved_rows(db_path) == []


def test_error_response_does_not_echo_input(client):
    response = client.post(URL, json=message(payload={**RADAR_STATUS, "fsp": 20}))
    assert all(set(error) == {"type", "loc", "msg"} for error in response.json()["detail"])


def test_unknown_device_is_rejected(client, db_path):
    assert client.post(URL, json=message(device_id="radar-9")).status_code == 403
    assert saved_rows(db_path) == []


@pytest.mark.parametrize(
    ("type_", "payload", "device_id"),
    [("rf.status", RF_STATUS, "radar-1"), ("radar.status", RADAR_STATUS, "pi-1")],
)
def test_message_not_matching_device_kind_is_rejected(client, db_path, type_, payload, device_id):
    response = client.post(URL, json=message(type_, payload, device_id=device_id))
    assert response.status_code == 403
    assert saved_rows(db_path) == []


def test_list_messages_returns_newest_first(client):
    first, second = message(), message()
    client.post(URL, json=first)
    client.post(URL, json=second)

    response = client.get(URL)

    assert response.status_code == 200
    assert [item["message"] for item in response.json()] == [second, first]
    assert all(item["received_at"] for item in response.json())


def test_list_messages_applies_limit(client):
    for _ in range(3):
        client.post(URL, json=message())

    assert len(client.get(URL, params={"limit": 2}).json()) == 2


def test_list_messages_excludes_rejected_messages(client):
    client.post(URL, json=message(device_id="radar-9"))
    assert client.get(URL).json() == []


@pytest.mark.parametrize("limit", [0, 501])
def test_list_messages_rejects_limit_out_of_range(client, limit):
    assert client.get(URL, params={"limit": limit}).status_code == 422


def test_repo_config_is_valid():
    config = load_config(REPO_CONFIG)
    assert config.devices["radar-1"].room in config.rooms
    assert config.devices["pi-1"].kind == "rf"


def test_device_with_unknown_room_is_rejected(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "rooms: {stall-1: {name: 칸 1, size_cm: {x: 97, y: 137.5, z: 228}, zones: {}}}\n"
        "devices: {radar-1: {kind: radar, room: stall-2}}\n"
        "device_timeout_sec: 30\n"
        "fusion_window_sec: 120\n"
        "cleaning_windows: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="stall-2"):
        load_config(path)


@pytest.mark.parametrize("timeout", ["device_timeout_sec: 0\n", ""])
def test_invalid_or_missing_timeout_is_rejected(tmp_path, timeout):
    path = tmp_path / "bad.yaml"
    path.write_text(
        "rooms: {stall-1: {name: 칸 1, size_cm: {x: 97, y: 137.5, z: 228}, zones: {}}}\n"
        "devices: {radar-1: {kind: radar, room: stall-1}}\n"
        "fusion_window_sec: 120\n"
        "cleaning_windows: []\n" + timeout,
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="device_timeout_sec"):
        load_config(path)
