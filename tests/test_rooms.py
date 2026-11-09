"""칸 평면도 설정과 레이더 구역(roi) 검사."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rafi.server.app import create_app
from rafi.server.config import Config, load_config

REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"
MESSAGES = "/api/v1/messages"


def detection(roi: str) -> dict:
    return {
        "schema": "1.0",
        "type": "radar.detection",
        "message_id": str(uuid4()),
        "device_id": "radar-1",
        "sent_at": "2026-11-09T14:00:30+09:00",
        "payload": {
            "source": "radar",
            "event_start": "2026-11-09T14:00:00+09:00",
            "event_end": "2026-11-09T14:00:20+09:00",
            "roi": roi,
            "score": 0.8,
            "method": "radar-v1",
            "reasons": [],
        },
    }


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(REPO_CONFIG, tmp_path / "rafi.db")) as test_client:
        yield test_client


# ---------- 레이더 roi ----------


@pytest.mark.parametrize("roi", ["vent", "shelf", "dispenser", "sanitary_box"])
def test_registered_zone_is_accepted(client, roi):
    assert client.post(MESSAGES, json=detection(roi)).json() == {"status": "accepted"}


def test_unknown_zone_is_rejected_without_saving(client):
    response = client.post(MESSAGES, json=detection("sanitary-box"))

    assert response.status_code == 422
    assert "sanitary-box" in response.json()["detail"]
    assert client.get(MESSAGES).json() == []
    assert client.get("/api/v1/alerts").json() == []


def test_fixture_is_not_a_zone(client):
    assert client.post(MESSAGES, json=detection("toilet")).status_code == 422


# ---------- 평면도 API ----------


def test_rooms_api_returns_layout_and_device_positions(client):
    room = client.get("/api/v1/rooms").json()["stall-1"]

    assert room["size_cm"] == {"x": 97, "y": 137.5, "z": 228}
    assert set(room["zones"]) == {"vent", "shelf", "dispenser", "sanitary_box"}
    assert room["zones"]["dispenser"]["y_cm"] == [79, 103.5]
    assert set(room["fixtures"]) == {"toilet", "door"}
    assert room["devices"]["radar-1"]["position_cm"] == {"x": 55, "y": 23, "z": 200}
    assert room["devices"]["pi-1"]["position_cm"] is None


# ---------- 설정 검사 ----------


def repo_config() -> dict:
    with open(REPO_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def stall(data: dict) -> dict:
    return data["rooms"]["stall-1"]


def test_repo_config_is_valid():
    assert "vent" in load_config(REPO_CONFIG).rooms["stall-1"].zones


def test_zone_outside_room_is_rejected():
    data = repo_config()
    stall(data)["zones"]["vent"]["x_cm"] = [90, 98]
    with pytest.raises(ValidationError, match="vent"):
        Config.model_validate(data)


def test_negative_coordinate_is_rejected():
    data = repo_config()
    stall(data)["fixtures"]["toilet"]["y_cm"] = [-1, 77]
    with pytest.raises(ValidationError, match="toilet"):
        Config.model_validate(data)


def test_reversed_range_is_rejected():
    data = repo_config()
    stall(data)["zones"]["shelf"]["y_cm"] = [22, 0]
    with pytest.raises(ValidationError, match="y_cm"):
        Config.model_validate(data)


def test_range_needs_two_values():
    data = repo_config()
    stall(data)["zones"]["shelf"]["z_cm"] = [142]
    with pytest.raises(ValidationError, match="z_cm"):
        Config.model_validate(data)


def test_same_id_in_zones_and_fixtures_is_rejected():
    data = repo_config()
    stall(data)["fixtures"]["vent"] = stall(data)["zones"]["vent"]
    with pytest.raises(ValidationError, match="같은 ID"):
        Config.model_validate(data)


def test_device_outside_room_is_rejected():
    data = repo_config()
    data["devices"]["radar-1"]["position_cm"]["z"] = 300
    with pytest.raises(ValidationError, match="radar-1"):
        Config.model_validate(data)
