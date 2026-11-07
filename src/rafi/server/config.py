"""서버 설정 파일 로드."""

from datetime import datetime, time
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import PositiveInt, field_validator, model_validator

from rafi.common.messages import Model, Source


class RoomConfig(Model):
    name: str


class DeviceConfig(Model):
    kind: Source
    room: str


class CleaningWindow(Model):
    start: time
    end: time

    @field_validator("start", "end", mode="before")
    @classmethod
    def require_text(cls, value: Any) -> Any:
        # YAML은 따옴표 없는 10:00을 숫자 600으로 읽어 엉뚱한 시각이 된다
        if not isinstance(value, str):
            raise ValueError('시각은 "10:00"처럼 따옴표로 감싸야 합니다')
        return value

    @model_validator(mode="after")
    def check_order(self) -> Self:
        if self.end <= self.start:
            raise ValueError("end는 start보다 늦어야 합니다")
        return self


class Config(Model):
    rooms: dict[str, RoomConfig]
    devices: dict[str, DeviceConfig]
    device_timeout_sec: PositiveInt  # 상태 메시지가 이 시간 동안 없으면 끊김으로 본다
    fusion_window_sec: PositiveInt  # 사건 구간 간격이 이 이하면 같은 사건으로 묶는다
    cleaning_windows: list[CleaningWindow]

    @model_validator(mode="after")
    def check_device_rooms(self) -> Self:
        for device_id, device in self.devices.items():
            if device.room not in self.rooms:
                raise ValueError(f"{device_id}의 room '{device.room}'이 rooms에 없습니다")
        return self

    def in_cleaning_window(self, at: datetime) -> bool:
        """서버 시간대 기준으로 청소 시간대인지 확인한다. 끝 시각은 포함하지 않는다."""
        local = at.astimezone().time()
        return any(window.start <= local < window.end for window in self.cleaning_windows)


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
