"""서버 설정 파일 로드."""

from datetime import datetime, time
from pathlib import Path
from typing import Any, Self

import yaml
from pydantic import Field, PositiveFloat, PositiveInt, field_validator, model_validator

from rafi.common.messages import Model, Source

# 칸 좌표의 원점은 뒷벽·좌측 칸막이·바닥이 만나는 모서리다.
# x: 좌측 칸막이 → 우측 칸막이, y: 뒷벽 → 문, z: 바닥 → 천장 (단위 cm)
Range = tuple[float, float]


class Size(Model):
    x: PositiveFloat
    y: PositiveFloat
    z: PositiveFloat


class Point(Model):
    x: float
    y: float
    z: float


class Area(Model):
    """칸 안의 직육면체 영역. 두께를 모르는 면은 최솟값과 최댓값을 같게 둔다."""

    name: str = Field(min_length=1)
    x_cm: Range
    y_cm: Range
    z_cm: Range

    @model_validator(mode="after")
    def check_order(self) -> Self:
        for axis, (low, high) in self._ranges().items():
            if low > high:
                raise ValueError(f"{axis}_cm의 앞 값이 뒤 값보다 큽니다")
        return self

    def _ranges(self) -> dict[str, Range]:
        return {"x": self.x_cm, "y": self.y_cm, "z": self.z_cm}

    def fits_in(self, size: Size) -> bool:
        limits = {"x": size.x, "y": size.y, "z": size.z}
        return all(
            0 <= low and high <= limits[axis] for axis, (low, high) in self._ranges().items()
        )


class RoomConfig(Model):
    name: str
    size_cm: Size
    zones: dict[str, Area]  # 경보 구역. 키가 레이더 roi 값이다
    fixtures: dict[str, Area] = {}  # 평면도 배경

    @model_validator(mode="after")
    def check_areas(self) -> Self:
        if shared := self.zones.keys() & self.fixtures.keys():
            raise ValueError(f"zones와 fixtures에 같은 ID가 있습니다: {sorted(shared)}")
        for area_id, area in {**self.zones, **self.fixtures}.items():
            if not area.fits_in(self.size_cm):
                raise ValueError(f"{area_id}의 범위가 칸 크기를 벗어납니다")
        return self


class DeviceConfig(Model):
    kind: Source
    room: str
    position_cm: Point | None = None  # 평면도 표시용. 모르면 생략한다


class RoomView(RoomConfig):
    """대시보드 평면도용. 칸 설정에 그 칸의 장비를 더한다."""

    devices: dict[str, DeviceConfig]


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
            position = device.position_cm
            if position is not None:
                as_area = Area(
                    name=device_id,
                    x_cm=(position.x, position.x),
                    y_cm=(position.y, position.y),
                    z_cm=(position.z, position.z),
                )
                if not as_area.fits_in(self.rooms[device.room].size_cm):
                    raise ValueError(f"{device_id}의 position_cm이 칸 크기를 벗어납니다")
        return self

    def room_views(self) -> dict[str, RoomView]:
        return {
            room_id: RoomView(
                **room.model_dump(),
                devices={
                    device_id: device
                    for device_id, device in self.devices.items()
                    if device.room == room_id
                },
            )
            for room_id, room in self.rooms.items()
        }

    def in_cleaning_window(self, at: datetime) -> bool:
        """서버 시간대 기준으로 청소 시간대인지 확인한다. 끝 시각은 포함하지 않는다."""
        local = at.astimezone().time()
        return any(window.start <= local < window.end for window in self.cleaning_windows)


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return Config.model_validate(yaml.safe_load(f))
