"""중앙 서버 FastAPI 앱.

실행: uvicorn --factory rafi.server.app:create_app --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from rafi.common.messages import SensorMessage
from rafi.server.config import load_config
from rafi.server.storage import Storage

DEFAULT_CONFIG_PATH = Path("config/rafi.yaml")
DEFAULT_DB_PATH = Path("data/rafi.db")


def create_app(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> FastAPI:
    config = load_config(config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.storage = Storage(db_path)
        yield
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

        accepted = request.app.state.storage.save_message(message, raw.decode("utf-8"))
        return {"status": "accepted" if accepted else "duplicate"}

    @app.get("/api/v1/messages")
    async def list_messages(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> list[dict]:
        return request.app.state.storage.list_messages(limit)

    return app
