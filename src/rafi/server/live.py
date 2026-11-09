"""대시보드로 보내는 실시간 메시지.

접속마다 대기열을 따로 두어, 느리거나 멈춘 브라우저가 센서 메시지 수신을 늦추지 않게 한다.
끊겨 있는 동안의 메시지는 보관하지 않는다. 대시보드는 재연결할 때 조회 API로 현재 상태를 읽는다.
"""

import asyncio
from datetime import datetime
from uuid import uuid4

import anyio
from fastapi import WebSocket, WebSocketDisconnect

from rafi.common.messages import SCHEMA_VERSION, Envelope

SERVER_DEVICE_ID = "server"
SEND_TIMEOUT_SEC = 5.0


def server_envelope(now: datetime) -> dict:
    """서버가 보내는 메시지의 공통 envelope 값."""
    return {
        "schema_version": SCHEMA_VERSION,
        "message_id": uuid4(),
        "device_id": SERVER_DEVICE_ID,
        "sent_at": now,
    }


class LiveHub:
    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[str]] = set()

    def broadcast(self, message: Envelope) -> None:
        """접속 중인 모든 대시보드의 대기열에 넣는다. 전송을 기다리지 않고 바로 돌아온다."""
        text = message.model_dump_json()
        for queue in self._queues:
            queue.put_nowait(text)

    async def serve(self, websocket: WebSocket) -> None:
        """접속 하나를 끊어질 때까지 처리한다. 전송이 실패하면 목록에서 제거한다."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._queues.add(queue)  # 접속 완료 직후의 메시지도 놓치지 않도록 먼저 등록한다
        try:
            await websocket.accept()
            # 전송과 끊김 감지를 함께 돌리다가 어느 한쪽이 끝나면 다른 쪽도 멈춘다
            async with anyio.create_task_group() as group:

                async def send() -> None:
                    try:
                        await _send_queued(websocket, queue)
                    except (TimeoutError, RuntimeError, WebSocketDisconnect):
                        pass  # 전송 실패: 이 접속만 정리하고 다른 접속과 수신에는 영향을 주지 않는다
                    group.cancel_scope.cancel()

                async def wait_closed() -> None:
                    await _wait_until_closed(websocket)
                    group.cancel_scope.cancel()

                group.start_soon(send)
                group.start_soon(wait_closed)
        finally:
            self._queues.discard(queue)


async def _send_queued(websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
    while True:
        text = await queue.get()
        with anyio.fail_after(SEND_TIMEOUT_SEC):
            await websocket.send_text(text)


async def _wait_until_closed(websocket: WebSocket) -> None:
    # 대시보드가 보내는 내용은 쓰지 않고, 연결이 끊긴 것만 알아챈다
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass
