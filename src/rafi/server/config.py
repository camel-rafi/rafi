"""서버 설정 파일 로드."""

from pathlib import Path
from typing import Self

import yaml
from pydantic import model_validator

from rafi.common.messages import Model, Source


class RoomConfig(Model):
    name: str


class DeviceConfig(Model):
    kind: Source
    room: str


class Config(Model):
    rooms: dict[str, RoomConfig]
    devices: dict[str, DeviceConfig]

    @model_validator(mode="after")
    def check_device_rooms(self) -> Self:
        for device_id, device in self.devices.items():
            if device.room not in self.rooms:
                raise ValueError(f"{device_id}의 room '{device.room}'이 rooms에 없습니다")
        return self


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
