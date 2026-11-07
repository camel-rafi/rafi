import pytest
from pydantic import ValidationError

from rafi.common.messages import (
    Alert,
    AlertCreatedMessage,
    AlertUpdatedMessage,
    Detection,
    DeviceStatus,
    DeviceStatusMessage,
    RadarDetectionMessage,
    RfWindowMessage,
    parse_sensor_message,
)

DETECTION = {
    "source": "radar",
    "event_start": "2026-10-20T14:02:51+09:00",
    "event_end": "2026-10-20T14:03:10+09:00",
    "roi": "vent",
    "device": None,
    "rssi_mean": None,
    "score": 0.81,
    "method": "rule-v1",
    "reasons": ["팔 높음 지속 19초", "몸통 이동량 낮음"],
}

RF_WINDOW = {
    "window_start": "2026-10-20T14:03:40+09:00",
    "window_sec": 1,
    "devices": [
        {
            "device": "cam-01",
            "frames_up": 412,
            "bytes_up": 531200,
            "frames_down": 38,
            "bytes_down": 2900,
            "size_p50": 1460,
            "size_p90": 1500,
            "gap_mean_ms": 2.4,
            "gap_std_ms": 1.1,
            "rssi_mean": -48,
        }
    ],
}

ALERT = {
    "alert_id": 17,
    "grade": "L2",
    "status": "unacked",
    "room": "stall-1",
    "roi": "vent",
    "started_at": "2026-10-20T14:02:51+09:00",
    "updated_at": "2026-10-20T14:03:42+09:00",
    "acked_at": None,
    "in_cleaning_window": False,
    "evidence": [
        {"source": "radar", "score": 0.81, "method": "rule-v1", "reasons": ["팔 높음 지속 19초"]},
        {"source": "rf", "score": 0.74, "method": "rf-rule-v1", "reasons": ["새 기기 업링크 지속 32초"]},
    ],
}


def envelope(type_: str, payload: dict, **overrides) -> dict:
    return {
        "schema": "1.0",
        "type": type_,
        "message_id": "7f3c2a9e-1b4d-4c8a-9e2f-5d6a7b8c9d0e",
        "device_id": "radar-1",
        "sent_at": "2026-10-20T14:03:11.200+09:00",
        "payload": payload,
        **overrides,
    }


@pytest.mark.parametrize(
    ("type_", "payload"),
    [
        ("radar.status", {"running": True, "fps": 20.1, "error": None}),
        ("radar.occupancy", {"person_count": 1, "zones": ["vent"]}),
        ("radar.detection", DETECTION),
        ("rf.status", {"capturing": True, "channel": 6, "dropped_frames": 0, "error": None}),
        ("rf.window", RF_WINDOW),
    ],
)
def test_sensor_messages_parse_by_type(type_, payload):
    message = parse_sensor_message(envelope(type_, payload))
    assert message.type == type_


def test_type_selects_payload_model():
    assert isinstance(parse_sensor_message(envelope("radar.detection", DETECTION)), RadarDetectionMessage)
    assert isinstance(parse_sensor_message(envelope("rf.window", RF_WINDOW)), RfWindowMessage)


def test_serialized_message_uses_schema_key():
    message = parse_sensor_message(envelope("radar.detection", DETECTION))
    dumped = message.model_dump(mode="json")
    assert dumped["schema"] == "1.0"
    assert "schema_version" not in dumped


def test_rf_window_allows_missing_gap_for_single_frame():
    device = {**RF_WINDOW["devices"][0], "frames_up": 1, "gap_mean_ms": None, "gap_std_ms": None}
    parse_sensor_message(envelope("rf.window", {**RF_WINDOW, "devices": [device]}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema": "2.0"},
        {"message_id": "not-a-uuid"},
        {"device_id": ""},
        {"sent_at": "2026-10-20T14:03:11"},  # 시간대 없음
        {"type": "unknown.type"},
        {"extra": 1},
    ],
)
def test_invalid_envelope_is_rejected(overrides):
    with pytest.raises(ValidationError):
        parse_sensor_message(envelope("radar.detection", DETECTION, **overrides))


@pytest.mark.parametrize(
    "changes",
    [
        {"score": 1.5},
        {"score": -0.1},
        {"event_end": "2026-10-20T14:02:50+09:00"},  # 시작보다 빠름
        {"event_start": "2026-10-20T14:02:51"},  # 시간대 없음
        {"roi": None},
        {"source": "rf", "device": "cam-01"},  # radar.detection의 출처는 레이더만 허용
        {"room": "stall-1"},  # room은 서버가 관리
        {"rssi_avg": -48},  # 규격에 없는 필드
    ],
)
def test_invalid_radar_detection_is_rejected(changes):
    with pytest.raises(ValidationError):
        parse_sensor_message(envelope("radar.detection", {**DETECTION, **changes}))


def test_negative_rf_count_is_rejected():
    device = {**RF_WINDOW["devices"][0], "frames_up": -1}
    with pytest.raises(ValidationError):
        parse_sensor_message(envelope("rf.window", {**RF_WINDOW, "devices": [device]}))


def test_alert_is_not_a_sensor_message():
    with pytest.raises(ValidationError):
        parse_sensor_message(envelope("alert.updated", ALERT))


def test_rf_candidate_needs_device():
    candidate = {**DETECTION, "source": "rf", "roi": None, "device": "cam-01", "rssi_mean": -48}
    assert Detection.model_validate(candidate).device == "cam-01"
    with pytest.raises(ValidationError):
        Detection.model_validate({**candidate, "device": None})


@pytest.mark.parametrize(
    ("model", "type_"),
    [(AlertCreatedMessage, "alert.created"), (AlertUpdatedMessage, "alert.updated")],
)
def test_alert_envelope_accepts_valid_alert(model, type_):
    message = model.model_validate(envelope(type_, ALERT, device_id="server"))
    assert message.payload.grade == "L2"


def test_device_status_envelope_accepts_valid_payload():
    payload = {
        "device_id": "radar-1",
        "kind": "radar",
        "online": True,
        "last_seen": "2026-10-20T14:04:05+09:00",
        "error": None,
    }
    message = DeviceStatusMessage.model_validate(envelope("device.status", payload, device_id="server"))
    assert message.payload.online is True


def test_device_status_allows_never_seen_device():
    payload = {"device_id": "pi-1", "kind": "rf", "online": False}
    assert DeviceStatus.model_validate(payload).last_seen is None


@pytest.mark.parametrize(
    ("type_", "payload"),
    [
        ("radar.status", {"running": True, "fps": -1}),
        ("radar.status", {"fps": 20.1}),
        ("radar.occupancy", {"person_count": -1, "zones": []}),
        ("radar.occupancy", {"person_count": 1, "zones": [], "room": "stall-1"}),
        ("rf.status", {"capturing": True, "channel": 0, "dropped_frames": 0}),
        ("rf.status", {"capturing": True, "channel": 6, "dropped_frames": -1}),
    ],
)
def test_invalid_status_and_occupancy_are_rejected(type_, payload):
    with pytest.raises(ValidationError):
        parse_sensor_message(envelope(type_, payload))


def test_l3_alert_has_no_roi():
    l3 = {**ALERT, "grade": "L3", "roi": None, "evidence": ALERT["evidence"][1:]}
    assert Alert.model_validate(l3).roi is None


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "acked"},  # acked_at 없음
        {"acked_at": "2026-10-20T14:05:00+09:00"},  # 미확인인데 acked_at 있음
        {"evidence": []},
    ],
)
def test_invalid_alert_is_rejected(changes):
    with pytest.raises(ValidationError):
        Alert.model_validate({**ALERT, **changes})
