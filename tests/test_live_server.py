"""실제 uvicorn 서버로 WebSocket을 확인한다.

TestClient는 서버 없이 앱을 직접 호출하므로, 서버에 WebSocket 라이브러리가 없어도 통과한다.
이 테스트는 그런 설치 문제를 잡기 위한 것이다.
"""

import json
import socket
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import uvicorn
from websockets.sync.client import connect

from rafi.server.app import create_app

REPO_CONFIG = Path(__file__).parents[1] / "config" / "rafi.yaml"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server_url(tmp_path: Path):
    port = free_port()
    config = uvicorn.Config(
        create_app(REPO_CONFIG, tmp_path / "rafi.db"), port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            pytest.fail("서버가 시작되지 않았습니다")
        time.sleep(0.05)

    yield f"127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


def test_dashboard_receives_messages_over_real_websocket(server_url):
    detection = {
        "schema": "1.0",
        "type": "radar.detection",
        "message_id": str(uuid4()),
        "device_id": "radar-1",
        "sent_at": "2026-11-09T14:00:30+09:00",
        "payload": {
            "source": "radar",
            "event_start": "2026-11-09T14:00:00+09:00",
            "event_end": "2026-11-09T14:00:20+09:00",
            "roi": "dispenser_top",
            "score": 0.8,
            "method": "radar-v1",
            "reasons": [],
        },
    }

    with connect(f"ws://{server_url}/api/v1/ws", open_timeout=5) as ws:
        response = httpx.post(f"http://{server_url}/api/v1/messages", json=detection)
        message = json.loads(ws.recv(timeout=5))

    assert response.json() == {"status": "accepted"}
    assert message["type"] == "alert.created"
    assert message["payload"]["roi"] == "dispenser_top"
