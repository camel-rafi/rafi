"""서버로 보내는 메시지 (팀 노션 '데이터 규칙 초안' schema 1.0, 프로젝트 문서 claude/rafi_system_contract.md).

모든 메시지는 공통 봉투에 담는다:
  {"schema": "1.0", "type": ..., "message_id": uuid, "device_id": ..., "sent_at": ISO 시각(+09:00), "payload": {...}}

서버 규격: camel-rafi/rafi의 src/rafi/common/messages.py (규격에 없는 칸이 있으면 서버가 422로 거부).
탐지기는 평가·화면 출력에 쓰는 내부 칸(event_id, ongoing, hidden, stand_height 등)을 payload에 같이 넣고,
서버·JSONL로 나갈 때만 to_server()가 규격 칸만 남긴다.

전송 방식: 레이더 PC → 서버는 HTTP POST (http://서버주소:8000/api/v1/messages, camel-rafi/rafi PR #5).
출력 방식(sink): 화면 출력, JSONL 파일(서버 담당 시험용), HTTP POST(--post로 서버 주소를 줄 때).
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
SCHEMA = "1.0"


def iso(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="milliseconds")


# 서버 규격(rafi.common.messages)의 payload 칸. 여기 없는 칸은 서버로 보내지 않는다.
SERVER_FIELDS = {
    "radar.detection": ("source", "event_start", "event_end", "roi", "device", "rssi_mean", "score", "method", "reasons"),
    "radar.status": ("sensor_ok", "fps", "error"),
    "radar.occupancy": ("person_count", "zones"),
}


def to_server(msg: dict) -> dict:
    """서버로 보낼 메시지: payload에서 규격 칸만 남긴 복사본."""
    keep = SERVER_FIELDS.get(msg["type"])
    if keep is None:
        return msg
    return {**msg, "payload": {k: msg["payload"][k] for k in keep if k in msg["payload"]}}


def envelope(msg_type: str, device_id: str, payload: dict, sent_at: datetime) -> dict:
    return {"schema": SCHEMA, "type": msg_type, "message_id": str(uuid.uuid4()), "device_id": device_id,
            "sent_at": iso(sent_at), "payload": payload}


class PrintSink:
    """화면에 한 줄씩. detection은 눈에 띄게, occupancy·status는 quiet이면 생략."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def send(self, msg: dict):
        p = msg["payload"]
        if msg["type"] == "radar.detection":
            state = "진행 중" if p.get("ongoing") else "끝남"
            print(f"[경보 {state}] {msg['sent_at'][11:19]} 구역 {p['roi']:14s} 점수 {p['score']:.2f}  "
                  f"{' / '.join(p['reasons'])}  (사건 {p['event_id']})", flush=True)
        elif not self.quiet:
            print(f"[{msg['type']}] {msg['sent_at'][11:19]} {json.dumps(p, ensure_ascii=False)}", flush=True)


class JsonlSink:
    """메시지를 한 줄에 하나씩 JSON으로 저장 (서버 규격 칸만. 서버 담당이 그대로 읽어 시험할 수 있게)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("a", encoding="utf-8")

    def send(self, msg: dict):
        self.f.write(json.dumps(to_server(msg), ensure_ascii=False) + "\n")
        self.f.flush()


class HttpSink:
    """서버에 HTTP POST (JSON 본문). 실패해도 탐지는 멈추지 않고 오류만 기록."""

    def __init__(self, url: str, timeout_s: float = 1.0):
        self.url, self.timeout_s = url, timeout_s
        self.errors = 0
        self.last_error = ""

    def send(self, msg: dict):
        data = json.dumps(to_server(msg), ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            urllib.request.urlopen(req, timeout=self.timeout_s).close()
        except urllib.error.HTTPError as e:  # 서버가 받았지만 거부 (422 = 규격 위반)
            self.errors += 1
            body = e.read().decode("utf-8", "replace")[:300]
            self.last_error = f"HTTP {e.code} ({msg['type']}): {body}"
        except Exception as e:  # noqa: BLE001 - 서버가 꺼져 있어도 탐지는 계속
            self.errors += 1
            self.last_error = f"{type(e).__name__}: {e}"
        else:
            return
        if self.errors in (1, 10, 100) or self.errors % 1000 == 0:
            print(f"[주의] 서버 전송 실패 {self.errors}회: {self.last_error}", file=sys.stderr, flush=True)


class ListSink:
    """평가용: 메모리에 모음."""

    def __init__(self):
        self.items: list[dict] = []

    def send(self, msg: dict):
        self.items.append(msg)