"""레이더가 보내는 메시지가 서버 규격(rafi.common.messages)과 맞는지 확인한다.

레이더 쪽 messages.py는 내부 칸(event_id, ongoing 등)을 빼고 서버 규격 칸만 보낸다(to_server).
서버 규격이 바뀌었는데 레이더 쪽 칸 목록을 안 고치면 여기서 실패한다.
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


@pytest.mark.parametrize(
    ("msg_type", "model"),
    [
        ("radar.detection", RadarDetection),
        ("radar.status", RadarStatus),
        ("radar.occupancy", RadarOccupancy),
    ],
)
def test_server_fields_match_schema(msg_type, model):
    assert set(SERVER_FIELDS[msg_type]) == set(model.model_fields)


def test_detection_with_internal_fields_passes_server_schema():
    now = datetime(2026, 11, 5, 13, 36, tzinfo=KST)
    payload = {
        "source": "radar",
        "event_start": (now - timedelta(seconds=12)).isoformat(),
        "event_end": now.isoformat(),
        "roi": "dispenser_low",
        "score": 0.91,
        "method": "rule-v2",
        "reasons": ["머리 숙임 12.0초", "디스펜서 앞·아래 손 머묾 1.4초"],
        # 레이더 내부 칸: 서버로 보낼 때 빠져야 한다
        "event_id": "radar-1-2",
        "ongoing": True,
    }
    msg = to_server(envelope("radar.detection", "radar-1", payload, now))
    assert "event_id" not in msg["payload"]
    parse_sensor_message(msg)


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
