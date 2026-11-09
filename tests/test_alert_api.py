"""경보 조회·확인 API와 WebSocket 알림."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from rafi.common.messages import DeviceStatus, DeviceStatusMessage
from rafi.server import live
from rafi.server.app import create_app
from rafi.server.live import LiveHub, server_envelope

MESSAGES = "/api/v1/messages"
ALERTS = "/api/v1/alerts"
WS = "/api/v1/ws"
REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"
RADAR_STATUS = {"sensor_ok": True, "fps": 20.1, "error": None}


def local(hour: int, minute: int = 0) -> datetime:
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


def radar_message(start: datetime, roi: str = "vent") -> dict:
    payload = {
        "source": "radar",
        "event_start": start.isoformat(),
        "event_end": (start + timedelta(seconds=10)).isoformat(),
        "roi": roi,
        "score": 0.8,
        "method": "radar-v1",
        "reasons": ["천장 부근 정지"],
    }
    return envelope("radar.detection", payload, "radar-1")


def send_rf_transmission(client, start: datetime) -> None:
    stats = {
        "device": "cam-01",
        "frames_up": 30,
        "bytes_up": 30_000,
        "frames_down": 2,
        "bytes_down": 200,
        "size_p50": 1000,
        "size_p90": 1400,
        "rssi_mean": -48,
    }
    for offset in range(15):
        window = (start + timedelta(seconds=offset)).isoformat()
        payload = {"window_start": window, "window_sec": 1, "devices": [stats]}
        client.post(MESSAGES, json=envelope("rf.window", payload, "pi-1"))


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(REPO_CONFIG, tmp_path / "rafi.db")) as test_client:
        yield test_client


# ---------- 조회 ----------


def test_alerts_start_empty(client):
    assert client.get(ALERTS).json() == []


def test_alert_contains_room_and_evidence(client):
    client.post(MESSAGES, json=radar_message(local(12)))

    alert = client.get(ALERTS).json()[0]

    assert alert["alert_id"] == 1
    assert alert["grade"] == "L1"
    assert alert["status"] == "unacked"
    assert alert["room"] == "stall-1"
    assert alert["roi"] == "vent"
    assert alert["acked_at"] is None
    assert alert["evidence"] == [
        {"source": "radar", "score": 0.8, "method": "radar-v1", "reasons": ["천장 부근 정지"]}
    ]


def test_promoted_alert_has_both_evidence(client):
    client.post(MESSAGES, json=radar_message(local(12)))
    send_rf_transmission(client, local(12))

    alert = client.get(ALERTS).json()[0]

    assert alert["grade"] == "L2"
    assert [e["source"] for e in alert["evidence"]] == ["radar", "rf"]


def test_alerts_are_newest_first_and_limited(client):
    client.post(MESSAGES, json=radar_message(local(12)))
    client.post(MESSAGES, json=radar_message(local(13)))

    assert [a["alert_id"] for a in client.get(ALERTS).json()] == [2, 1]
    assert [a["alert_id"] for a in client.get(ALERTS, params={"limit": 1}).json()] == [2]


def test_alerts_filter_by_status(client):
    client.post(MESSAGES, json=radar_message(local(12)))
    client.post(MESSAGES, json=radar_message(local(13)))
    client.post(f"{ALERTS}/1/ack")

    assert [a["alert_id"] for a in client.get(ALERTS, params={"status": "acked"}).json()] == [1]
    assert [a["alert_id"] for a in client.get(ALERTS, params={"status": "unacked"}).json()] == [2]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"status": "unknown"}])
def test_invalid_alert_query_is_rejected(client, params):
    assert client.get(ALERTS, params=params).status_code == 422


# ---------- 확인 ----------


def test_ack_marks_alert(client):
    client.post(MESSAGES, json=radar_message(local(12)))

    response = client.post(f"{ALERTS}/1/ack")

    assert response.status_code == 200
    assert response.json()["status"] == "acked"
    assert response.json()["acked_at"] is not None
    assert client.get(ALERTS).json()[0] == response.json()


def test_second_ack_keeps_first_time(client):
    client.post(MESSAGES, json=radar_message(local(12)))
    first = client.post(f"{ALERTS}/1/ack").json()

    second = client.post(f"{ALERTS}/1/ack")

    assert second.status_code == 200
    assert second.json() == first


def test_ack_unknown_alert_is_404(client):
    assert client.post(f"{ALERTS}/99/ack").status_code == 404


def test_signal_after_ack_creates_new_alert(client):
    client.post(MESSAGES, json=radar_message(local(12)))
    client.post(f"{ALERTS}/1/ack")

    send_rf_transmission(client, local(12))

    assert [(a["alert_id"], a["grade"]) for a in client.get(ALERTS).json()] == [
        (2, "L3"),
        (1, "L1"),
    ]


# ---------- WebSocket ----------


def test_new_alert_is_pushed_as_created(client):
    with client.websocket_connect(WS) as ws:
        client.post(MESSAGES, json=radar_message(local(12)))
        message = ws.receive_json()

    assert message["schema"] == "1.0"
    assert message["type"] == "alert.created"
    assert message["device_id"] == "server"
    assert message["payload"] == client.get(ALERTS).json()[0]


def test_promotion_is_pushed_as_updated(client):
    client.post(MESSAGES, json=radar_message(local(12)))

    with client.websocket_connect(WS) as ws:
        send_rf_transmission(client, local(12))
        message = ws.receive_json()

    assert message["type"] == "alert.updated"
    assert message["payload"]["grade"] == "L2"


def test_rf_promoting_two_alerts_pushes_both(client):
    client.post(MESSAGES, json=radar_message(local(12), "vent"))
    client.post(MESSAGES, json=radar_message(local(12), "door"))

    with client.websocket_connect(WS) as ws:
        send_rf_transmission(client, local(12))
        ids = {ws.receive_json()["payload"]["alert_id"], ws.receive_json()["payload"]["alert_id"]}

    assert ids == {1, 2}


def test_ack_is_pushed_once(client):
    client.post(MESSAGES, json=radar_message(local(12)))

    with client.websocket_connect(WS) as ws:
        client.post(f"{ALERTS}/1/ack")
        client.post(f"{ALERTS}/1/ack")  # 두 번째 확인은 알리지 않는다
        client.post(MESSAGES, json=radar_message(local(13)))

        first, second = ws.receive_json(), ws.receive_json()

    assert (first["type"], first["payload"]["status"]) == ("alert.updated", "acked")
    assert second["type"] == "alert.created"


def test_duplicate_message_is_not_pushed(client):
    message = radar_message(local(12))
    with client.websocket_connect(WS) as ws:
        client.post(MESSAGES, json=message)
        client.post(MESSAGES, json=message)
        client.post(MESSAGES, json=radar_message(local(13)))

        first, second = ws.receive_json(), ws.receive_json()

    assert (first["payload"]["alert_id"], second["payload"]["alert_id"]) == (1, 2)


def test_every_dashboard_receives_and_closed_one_does_not_block(client):
    with client.websocket_connect(WS) as staying:
        with client.websocket_connect(WS) as leaving:
            client.post(MESSAGES, json=radar_message(local(12)))
            assert leaving.receive_json()["type"] == "alert.created"
        assert staying.receive_json()["payload"]["alert_id"] == 1

        response = client.post(MESSAGES, json=radar_message(local(13)))

        assert response.status_code == 200
        assert staying.receive_json()["payload"]["alert_id"] == 2


def test_device_change_is_pushed(client):
    with client.websocket_connect(WS) as ws:
        client.post(MESSAGES, json=envelope("radar.status", RADAR_STATUS, "radar-1"))
        message = ws.receive_json()  # 1초 주기 확인에서 변경을 찾아 보낸다

    assert message["type"] == "device.status"
    assert message["payload"]["device_id"] == "radar-1"
    assert message["payload"]["online"] is True


def test_rolled_back_alert_is_not_pushed(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("융합 실패")

    app = create_app(REPO_CONFIG, tmp_path / "rafi.db")
    with TestClient(app, raise_server_exceptions=False) as client:
        with client.websocket_connect(WS) as ws:
            monkeypatch.setattr("rafi.server.alerts.fuse", fail)
            assert client.post(MESSAGES, json=radar_message(local(12))).status_code == 500
            monkeypatch.undo()

            client.post(MESSAGES, json=radar_message(local(13)))
            message = ws.receive_json()

    assert message["payload"]["started_at"] == local(13).isoformat()


# ---------- 멈춘 대시보드 ----------


class StuckWebSocket:
    """전송이 끝나지 않는 브라우저."""

    async def accept(self) -> None:
        pass

    async def send_text(self, text: str) -> None:
        await asyncio.Event().wait()

    async def receive(self) -> dict:
        await asyncio.Event().wait()


def test_stuck_dashboard_is_removed_without_delaying_broadcast(monkeypatch):
    monkeypatch.setattr(live, "SEND_TIMEOUT_SEC", 0.01)
    now = datetime(2026, 11, 7, 12, 0).astimezone()
    status = DeviceStatus(device_id="radar-1", kind="radar", online=True)
    message = DeviceStatusMessage(type="device.status", payload=status, **server_envelope(now))

    async def scenario() -> None:
        hub = LiveHub()
        serving = asyncio.create_task(hub.serve(StuckWebSocket()))
        await asyncio.sleep(0)

        hub.broadcast(message)  # 기다리지 않고 바로 돌아와야 한다

        await asyncio.wait_for(serving, timeout=1)
        assert hub._queues == set()

    asyncio.run(scenario())
