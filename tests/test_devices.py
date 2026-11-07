from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from rafi.server.app import create_app
from rafi.server.config import DeviceConfig
from rafi.server.devices import NO_STATUS_ERROR, DeviceTracker

REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
TIMEOUT = timedelta(seconds=30)

RADAR_STATUS = {"sensor_ok": True, "fps": 20.1, "error": None}
RF_STATUS = {"sensor_ok": True, "channel": 6, "dropped_frames": 0, "error": None}


@pytest.fixture
def tracker() -> DeviceTracker:
    devices = {
        "radar-1": DeviceConfig(kind="radar", room="stall-1"),
        "pi-1": DeviceConfig(kind="rf", room="stall-1"),
    }
    return DeviceTracker(devices, TIMEOUT)


def status_of(tracker: DeviceTracker, device_id: str, now: datetime):
    return next(s for s in tracker.statuses(now) if s.device_id == device_id)


def test_device_starts_offline_with_no_status_error(tracker):
    status = status_of(tracker, "radar-1", T0)
    assert status.online is False
    assert status.last_seen is None
    assert status.error == NO_STATUS_ERROR


def test_device_is_online_after_status(tracker):
    tracker.update("radar-1", True, None, T0)
    status = status_of(tracker, "radar-1", T0 + timedelta(seconds=10))
    assert status.online is True
    assert status.last_seen == T0
    assert status.error is None


def test_devices_are_tracked_separately(tracker):
    tracker.update("radar-1", True, None, T0)
    assert status_of(tracker, "radar-1", T0).online is True
    assert status_of(tracker, "pi-1", T0).online is False


def test_device_stays_online_just_before_timeout(tracker):
    tracker.update("radar-1", True, None, T0)
    assert status_of(tracker, "radar-1", T0 + TIMEOUT - timedelta(seconds=1)).online is True


def test_timeout_marks_offline_and_replaces_old_error(tracker):
    tracker.update("radar-1", True, "오래된 오류", T0)
    status = status_of(tracker, "radar-1", T0 + TIMEOUT)
    assert status.online is False
    assert status.error == NO_STATUS_ERROR
    assert status.last_seen == T0


def test_device_recovers_after_timeout(tracker):
    tracker.update("radar-1", True, None, T0)
    later = T0 + TIMEOUT * 2
    tracker.update("radar-1", True, None, later)
    status = status_of(tracker, "radar-1", later)
    assert status.online is True
    assert status.error is None


def test_sensor_failure_is_offline_with_device_error(tracker):
    tracker.update("radar-1", False, "프레임 수신 없음", T0)
    status = status_of(tracker, "radar-1", T0)
    assert status.online is False
    assert status.error == "프레임 수신 없음"
    assert status.last_seen == T0


def test_sensor_recovery_clears_error(tracker):
    tracker.update("radar-1", False, "프레임 수신 없음", T0)
    tracker.update("radar-1", True, None, T0 + timedelta(seconds=10))
    status = status_of(tracker, "radar-1", T0 + timedelta(seconds=10))
    assert status.online is True
    assert status.error is None


# ---------- API ----------


def message(type_: str, payload: dict, device_id: str = "radar-1", **overrides) -> dict:
    return {
        "schema": "1.0",
        "type": type_,
        "message_id": str(uuid4()),
        "device_id": device_id,
        "sent_at": "2020-01-01T00:00:00+09:00",  # 장비 시계가 크게 틀어진 경우
        "payload": payload,
        **overrides,
    }


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(REPO_CONFIG, tmp_path / "rafi.db")) as test_client:
        yield test_client


def devices_by_id(client) -> dict:
    return {d["device_id"]: d for d in client.get("/api/v1/devices").json()}


def test_all_configured_devices_start_offline(client):
    devices = devices_by_id(client)
    assert set(devices) == {"radar-1", "pi-1"}
    assert devices["radar-1"]["kind"] == "radar"
    assert devices["pi-1"]["kind"] == "rf"
    assert not any(d["online"] for d in devices.values())


def test_status_messages_update_each_device(client):
    client.post("/api/v1/messages", json=message("radar.status", RADAR_STATUS))
    assert devices_by_id(client)["radar-1"]["online"] is True
    assert devices_by_id(client)["pi-1"]["online"] is False

    client.post("/api/v1/messages", json=message("rf.status", RF_STATUS, device_id="pi-1"))
    assert devices_by_id(client)["pi-1"]["online"] is True


def test_last_seen_uses_server_time_not_device_clock(client):
    client.post("/api/v1/messages", json=message("radar.status", RADAR_STATUS))
    device = devices_by_id(client)["radar-1"]
    assert device["online"] is True
    assert not device["last_seen"].startswith("2020")


def test_sensor_failure_status_is_reported(client):
    payload = {"sensor_ok": False, "fps": 0, "error": "프레임 수신 없음"}
    client.post("/api/v1/messages", json=message("radar.status", payload))
    device = devices_by_id(client)["radar-1"]
    assert device["online"] is False
    assert device["error"] == "프레임 수신 없음"


def test_non_status_message_does_not_mark_online(client):
    payload = {"person_count": 0, "zones": []}
    client.post("/api/v1/messages", json=message("radar.occupancy", payload))
    assert devices_by_id(client)["radar-1"]["online"] is False


def test_duplicate_status_does_not_refresh_last_seen(client):
    msg = message("radar.status", RADAR_STATUS)
    client.post("/api/v1/messages", json=msg)
    first_seen = devices_by_id(client)["radar-1"]["last_seen"]

    assert client.post("/api/v1/messages", json=msg).json() == {"status": "duplicate"}
    assert devices_by_id(client)["radar-1"]["last_seen"] == first_seen


def test_rejected_status_does_not_update_device(client):
    client.post("/api/v1/messages", json=message("rf.status", RF_STATUS))  # radar-1이 RF 메시지 전송
    assert devices_by_id(client)["radar-1"]["online"] is False
