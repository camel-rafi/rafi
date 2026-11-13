"""서버로 메시지 보내기. 모듈 연동 규약의 응답 처리와 재전송 규칙을 따른다."""

import time
from collections.abc import Callable
from typing import Protocol

import httpx

from rafi.common.messages import Envelope

MESSAGES_PATH = "/api/v1/messages"
RETRIES = 3
RETRY_DELAY_SEC = 1.0


class Response(Protocol):
    status_code: int

    def json(self) -> dict: ...


class RejectedError(Exception):
    """서버가 거부한 메시지(4xx). 재전송해도 같은 결과이므로 코드를 고쳐야 한다."""


class Sender:
    def __init__(
        self,
        post: Callable[[dict], Response],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # post를 바꿔 끼우면 실제 서버 대신 테스트용 앱으로 보낼 수 있다
        self._post = post
        self._sleep = sleep

    def send(self, message: Envelope) -> str:
        """전송 결과("accepted", "duplicate", "failed")를 돌려준다."""
        body = message.model_dump(mode="json")
        # 놓치면 경보가 사라지는 판단만 같은 message_id로 재전송한다
        attempts = RETRIES + 1 if message.type == "radar.detection" else 1
        for attempt in range(attempts):
            if attempt:
                self._sleep(RETRY_DELAY_SEC)
            try:
                response = self._post(body)
            except httpx.TransportError:
                continue  # 서버가 꺼져 있거나 네트워크 문제
            if response.status_code >= 500:
                continue
            if response.status_code >= 400:
                raise RejectedError(f"{message.type} 거부 ({response.status_code}): {response.json()}")
            return response.json()["status"]
        return "failed"


def http_sender(client: httpx.Client) -> Sender:
    return Sender(lambda body: client.post(MESSAGES_PATH, json=body))
