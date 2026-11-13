"""가짜 송신기 시나리오가 서버에서 예상한 결과를 내는지 확인한다."""

from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from rafi.common.messages import (
    RadarDetection,
    RadarDetectionMessage,
    RadarStatus,
    RadarStatusMessage,
)
from rafi.server.app import create_app
from rafi.sim.scenarios import SCENARIOS, Clock, Simulator, demo_sequence
from rafi.sim.sender import MESSAGES_PATH, RejectedError, Sender

REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"
START = datetime(2026, 11, 9, 14, 0).astimezone()


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(REPO_CONFIG, tmp_path / "rafi.db")) as test_client:
        yield test_client


def run(client, *names: str) -> None:
    sender = Sender(lambda body: client.post(MESSAGES_PATH, json=body))
    simulator = Simulator(sender, Clock(START, realtime=False), log=lambda line: None)
    for name in names:
        simulator.run(SCENARIOS[name])


def alerts(client) -> list[tuple]:
    return [(a["grade"], a["roi"]) for a in reversed(client.get("/api/v1/alerts").json())]


def radar_device(client) -> dict:
    return next(d for d in client.get("/api/v1/devices").json() if d["device_id"] == "radar-1")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("radar-only", [("L1", "dispenser_top")]),
        ("rf-only", [("L3", None)]),
        ("radar-then-rf", [("L2", "dispenser_top")]),
        ("rf-then-radar", [("L2", "dispenser_top")]),
        ("two-zones", [("L2", "dispenser_top"), ("L2", "sanbox_top")]),
    ],
)
def test_scenario_produces_expected_alerts(client, name, expected):
    run(client, name)
    assert alerts(client) == expected


def test_idle_rf_traffic_creates_no_alert(client):
    run(client, "sensor-fail")  # 카메라 전송 없이 rf.window만 계속 보낸다
    assert alerts(client) == []


def test_sensor_fail_reports_module_error(client):
    run(client, "sensor-fail")
    device = radar_device(client)
    assert device["online"] is False
    assert "가짜 송신기" in device["error"]


def test_device_down_stops_radar_status(client):
    run(client, "device-down")
    messages = [m["message"] for m in client.get("/api/v1/messages", params={"limit": 500}).json()]
    radar_status_times = [m["sent_at"] for m in messages if m["type"] == "radar.status"]
    assert len(radar_status_times) == 1  # 시작할 때 한 번 보내고 멈춘다


def test_demo_keeps_scenarios_separate(client):
    sender = Sender(lambda body: client.post(MESSAGES_PATH, json=body))
    simulator = Simulator(sender, Clock(START, realtime=False), log=lambda line: None)
    for scenario in demo_sequence():
        simulator.run(scenario)

    assert [grade for grade, _ in alerts(client)] == ["L1", "L3", "L2", "L2", "L2", "L2"]


# ---------- 전송 규칙 ----------


class FakeResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def detection_message() -> RadarDetectionMessage:
    payload = RadarDetection(
        source="radar",
        event_start=START,
        event_end=START,
        roi="dispenser_top",
        score=0.8,
        method="fake-v1",
        reasons=[],
    )
    return RadarDetectionMessage(
        type="radar.detection",
        payload=payload,
        schema_version="1.0",
        message_id="11111111-1111-4111-8111-111111111111",
        device_id="radar-1",
        sent_at=START,
    )


def status_message() -> RadarStatusMessage:
    return RadarStatusMessage(
        type="radar.status",
        payload=RadarStatus(sensor_ok=True, fps=20),
        schema_version="1.0",
        message_id="22222222-2222-4222-8222-222222222222",
        device_id="radar-1",
        sent_at=START,
    )


def sender_with(responses: list) -> tuple[Sender, list[dict]]:
    sent: list[dict] = []

    def post(body: dict):
        sent.append(body)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    return Sender(post, sleep=lambda seconds: None), sent


def test_detection_is_retried_with_same_message_id():
    sender, sent = sender_with(
        [httpx.ConnectError("꺼짐"), FakeResponse(503, {}), FakeResponse(200, {"status": "accepted"})]
    )
    assert sender.send(detection_message()) == "accepted"
    assert len({body["message_id"] for body in sent}) == 1
    assert len(sent) == 3


def test_detection_gives_up_after_retries():
    sender, sent = sender_with([httpx.ConnectError("꺼짐")] * 4)
    assert sender.send(detection_message()) == "failed"
    assert len(sent) == 4


def test_status_is_not_retried():
    sender, sent = sender_with([httpx.ConnectError("꺼짐")])
    assert sender.send(status_message()) == "failed"
    assert len(sent) == 1


def test_rejected_message_raises():
    sender, _ = sender_with([FakeResponse(422, {"detail": "형식 오류"})])
    with pytest.raises(RejectedError, match="422"):
        sender.send(detection_message())


def test_message_body_uses_spec_field_names():
    sender, sent = sender_with([FakeResponse(200, {"status": "accepted"})])
    sender.send(status_message())
    assert sent[0]["schema"] == "1.0"
    assert "schema_version" not in sent[0]
