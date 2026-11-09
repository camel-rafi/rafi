"""대시보드 파일 제공. 화면 동작은 가짜 송신기 demo로 직접 확인한다."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rafi.server.app import create_app

REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(REPO_CONFIG, tmp_path / "rafi.db")) as test_client:
        yield test_client


def test_dashboard_is_served_at_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "RaFi 관리자" in response.text


@pytest.mark.parametrize("path", ["/app.js", "/style.css"])
def test_dashboard_assets_are_served(client, path):
    assert client.get(path).status_code == 200


def test_api_is_not_shadowed_by_dashboard(client):
    assert client.get("/api/v1/alerts").json() == []
    assert client.get("/api/v1/unknown").status_code == 404


def test_dashboard_uses_no_external_files(client):
    # 핫스팟에 인터넷이 없을 수 있다
    for path in ["/", "/app.js", "/style.css"]:
        assert "http://" not in client.get(path).text.replace("http://www.w3.org", "")
        assert "https://" not in client.get(path).text


def test_rooms_include_cleaning_windows(client):
    room = client.get("/api/v1/rooms").json()["stall-1"]
    assert room["cleaning_windows"] == [
        {"start": "10:00:00", "end": "10:30:00"},
        {"start": "16:00:00", "end": "16:30:00"},
    ]
