"""레이더가 보내는 메시지가 서버 규격(rafi.common.messages)과 맞는지 확인한다.

레이더 쪽 messages.py는 내부 칸(ongoing 등)을 빼고 서버 규격 칸만 보낸다(to_server).
서버 규격이 바뀌었는데 레이더 쪽 칸 목록을 안 고치면 여기서 실패한다.

event_id(규약 8번)는 서버가 먼저 받을 수 있게 됐고, 레이더가 보내도록 바꾸는 것은 레이더 담당 몫이다.
그 전까지는 레이더가 아직 안 보내는 서버 칸으로 두고(RADAR_PENDING),
레이더가 보내기 시작하면 test_radar_sends_event_id가 XPASS로 실패해 정리할 때를 알려 준다.
"""

from datetime import datetime, timedelta

import pytest

from rafi.common.messages import (
    RadarDetection,
    RadarOccupancy,
    RadarStatus,
    parse_sensor_message,
)
from rafi.radar.messages import KST, SERVER_FIELDS, envelope, to_server

# 서버 규격에는 있지만 레이더가 아직 보내지 않는 칸 (레이더가 보내기 시작하면 지운다)
RADAR_PENDING = {"radar.detection": {"event_id"}}


@pytest.mark.parametrize(
    ("msg_type", "model"),
    [
        ("radar.detection", RadarDetection),
        ("radar.status", RadarStatus),
        ("radar.occupancy", RadarOccupancy),
    ],
)
def test_server_fields_match_schema(msg_type, model):
    sent = set(SERVER_FIELDS[msg_type])
    schema = set(model.model_fields)
    assert sent <= schema  # 서버가 모르는 칸을 보내면 422로 거부된다
    assert schema - sent <= RADAR_PENDING.get(msg_type, set())


def detection_payload(now: datetime) -> dict:
    return {
        "source": "radar",
        "event_start": (now - timedelta(seconds=12)).isoformat(),
        "event_end": now.isoformat(),
        "roi": "dispenser_low",
        "score": 0.91,
        "method": "rule-v2",
        "reasons": ["머리 숙임 12.0초", "디스펜서 앞·아래 손 머묾 1.4초"],
        "event_id": "3f1c2b7e-0d4a-4e8b-9c1f-2a6d5e8b7c90",
        # 레이더 내부 칸: 서버로 보낼 때 빠져야 한다
        "ongoing": True,
    }


def test_detection_with_internal_fields_passes_server_schema():
    now = datetime(2026, 11, 5, 13, 36, tzinfo=KST)
    msg = to_server(envelope("radar.detection", "radar-1", detection_payload(now), now))
    assert "ongoing" not in msg["payload"]
    parse_sensor_message(msg)


@pytest.mark.xfail(
    strict=True,
    reason="규약 8번: 레이더 담당이 SERVER_FIELDS에 event_id를 넣으면 통과한다. "
    "그때 이 표시와 RADAR_PENDING의 event_id를 지운다",
)
def test_radar_sends_event_id():
    now = datetime(2026, 11, 5, 13, 36, tzinfo=KST)
    payload = detection_payload(now)
    msg = to_server(envelope("radar.detection", "radar-1", payload, now))
    assert msg["payload"].get("event_id") == payload["event_id"]
    assert parse_sensor_message(msg).payload.event_id == payload["event_id"]


def test_status_and_occupancy_pass_server_schema():
    now = datetime(2026, 11, 5, 13, 36, tzinfo=KST)
    status = envelope("radar.status", "radar-1", {"sensor_ok": True, "fps": 18.5, "error": None}, now)
    occupancy = envelope(
        "radar.occupancy",
        "radar-1",
        {"person_count": 1, "zones": ["dispenser_low"], "hidden": False, "stand_height": 1.37},
        now,
    )
    parse_sensor_message(to_server(status))
    parse_sensor_message(to_server(occupancy))
