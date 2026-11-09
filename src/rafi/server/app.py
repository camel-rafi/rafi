"""중앙 서버 FastAPI 앱.

실행: uvicorn --factory rafi.server.app:create_app --host 0.0.0.0 --port 8000
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rafi.common.messages import (
    Alert,
    AlertStatus,
    AlertUpdatedMessage,
    Detection,
    DeviceStatus,
    DeviceStatusMessage,
    RadarDetectionMessage,
    RadarStatusMessage,
    RfStatusMessage,
    RfWindowMessage,
    SensorMessage,
)
from rafi.rf.judge import RfJudge
from rafi.server.alerts import record_detection
from rafi.server.config import load_config
from rafi.server.devices import DeviceTracker
from rafi.server.live import LiveHub, server_envelope
from rafi.server.storage import Storage

DEFAULT_CONFIG_PATH = Path("config/rafi.yaml")
DEFAULT_DB_PATH = Path("data/rafi.db")
DEVICE_CHECK_SEC = 1


def create_app(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> FastAPI:
    config = load_config(config_path)
    devices = DeviceTracker(config.devices, timedelta(seconds=config.device_timeout_sec))
    rf_judge = RfJudge()
    hub = LiveHub()

    async def watch_devices() -> None:
        # 장비가 끊기면 메시지가 오지 않으므로 수신 시점이 아니라 주기적으로 확인한다
        while True:
            await asyncio.sleep(DEVICE_CHECK_SEC)
            now = datetime.now().astimezone()
            for status in devices.changes(now):
                hub.broadcast(
                    DeviceStatusMessage(
                        type="device.status", payload=status, **server_envelope(now)
                    )
                )

    def detections_from(message: SensorMessage) -> list[Detection]:
        if isinstance(message, RadarDetectionMessage):
            return [message.payload]
        if isinstance(message, RfWindowMessage):
            return rf_judge.update(message.device_id, message.payload)
        return []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.storage = Storage(db_path)
        watcher = asyncio.create_task(watch_devices())
        yield
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        app.state.storage.close()

    app = FastAPI(title="RaFi 중앙 서버", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 기본 처리기는 입력값을 응답에 담는데, UTF-8이 아닌 본문이면 이 과정에서 500이 난다
        detail = [{"type": e["type"], "loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": detail})

    @app.post("/api/v1/messages")
    async def receive_message(message: SensorMessage, request: Request) -> dict[str, str]:
        raw = await request.body()  # 검증된 객체와 별개로 받은 본문 그대로 저장한다

        device = config.devices.get(message.device_id)
        if device is None:
            raise HTTPException(status_code=403, detail="등록되지 않은 장비입니다")
        if message.type.split(".")[0] != device.kind:
            raise HTTPException(status_code=403, detail="장비 종류와 맞지 않는 메시지입니다")

        storage = request.app.state.storage
        now = datetime.now().astimezone()
        alert_messages = []
        with storage.transaction():  # 메시지는 저장됐는데 경보만 빠지는 일이 없도록 함께 처리한다
            accepted = storage.save_message(message, raw.decode("utf-8"))
            if accepted:
                for detection in detections_from(message):
                    alert_messages += record_detection(
                        storage, detection, device.room, config, now
                    )

        # 취소된 경보를 화면에 띄우지 않도록 저장이 끝난 뒤에 알린다
        for alert_message in alert_messages:
            hub.broadcast(alert_message)

        if accepted and isinstance(message, RadarStatusMessage | RfStatusMessage):
            devices.update(
                message.device_id,
                message.payload.sensor_ok,
                message.payload.error,
                now=now,
            )
        return {"status": "accepted" if accepted else "duplicate"}

    @app.get("/api/v1/devices")
    async def list_devices() -> list[DeviceStatus]:
        return devices.statuses(now=datetime.now().astimezone())

    @app.get("/api/v1/alerts")
    async def list_alerts(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
        status: AlertStatus | None = None,
    ) -> list[Alert]:
        return request.app.state.storage.list_alerts(limit, status)

    @app.post("/api/v1/alerts/{alert_id}/ack")
    async def ack_alert(alert_id: int, request: Request) -> Alert:
        storage = request.app.state.storage
        now = datetime.now().astimezone()
        with storage.transaction():
            changed = storage.ack_alert(alert_id, now)
        alert = storage.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="없는 경보입니다")
        if changed:  # 이미 확인된 경보면 첫 확인 시각을 유지하고 알리지 않는다
            hub.broadcast(
                AlertUpdatedMessage(type="alert.updated", payload=alert, **server_envelope(now))
            )
        return alert

    @app.websocket("/api/v1/ws")
    async def live_updates(websocket: WebSocket) -> None:
        await hub.serve(websocket)

    @app.get("/api/v1/messages")
    async def list_messages(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        return request.app.state.storage.list_messages(limit)

    return app
