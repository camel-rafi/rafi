"""모듈 간 메시지 규격 (schema 1.0).

모든 메시지는 같은 envelope에 담기며, `type`에 따라 payload 형식이 정해진다.
칸(room)은 센서가 보내지 않는다. 서버가 `device_id`로 설정 파일에서 찾아 경보에 붙인다.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
    model_validator,
)

SCHEMA_VERSION = "1.0"

Source = Literal["radar", "rf"]
Grade = Literal["L1", "L2", "L3"]
AlertStatus = Literal["unacked", "acked"]


class Model(BaseModel):
    """규격에 없는 필드는 거부한다. 필드명 오타로 값이 조용히 사라지는 것을 막기 위함이다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


# ---------- payload: 판단 ----------


class Detection(Model):
    """센서 판단의 공통 형식. radar.detection과 서버 내부 rf.candidate가 함께 쓴다."""

    source: Source
    event_start: AwareDatetime
    event_end: AwareDatetime
    roi: str | None = None
    device: str | None = None
    rssi_mean: float | None = None
    score: float = Field(ge=0, le=1)
    method: str = Field(min_length=1)
    reasons: list[str]

    @model_validator(mode="after")
    def check_consistency(self) -> Self:
        if self.event_end < self.event_start:
            raise ValueError("event_end는 event_start보다 빠를 수 없습니다")
        if self.source == "radar" and self.roi is None:
            raise ValueError("레이더 판단에는 roi가 필요합니다")
        if self.source == "rf" and self.device is None:
            raise ValueError("RF 판단에는 device가 필요합니다")
        return self


class RadarDetection(Detection):
    source: Literal["radar"]


# ---------- payload: 레이더 ----------


class RadarStatus(Model):
    running: bool
    fps: NonNegativeFloat
    error: str | None = None


class RadarOccupancy(Model):
    person_count: NonNegativeInt
    zones: list[str]


# ---------- payload: RF ----------


class RfStatus(Model):
    capturing: bool
    channel: PositiveInt
    dropped_frames: NonNegativeInt
    error: str | None = None


class RfDeviceStats(Model):
    device: str = Field(min_length=1)
    frames_up: NonNegativeInt
    bytes_up: NonNegativeInt
    frames_down: NonNegativeInt
    bytes_down: NonNegativeInt
    size_p50: NonNegativeFloat
    size_p90: NonNegativeFloat
    gap_mean_ms: NonNegativeFloat | None = None  # 프레임이 1개면 간격을 계산할 수 없다
    gap_std_ms: NonNegativeFloat | None = None
    rssi_mean: float


class RfWindow(Model):
    window_start: AwareDatetime
    window_sec: PositiveInt
    devices: list[RfDeviceStats]


# ---------- payload: 서버 -> 대시보드 ----------


class Evidence(Model):
    source: Source
    score: float = Field(ge=0, le=1)
    method: str = Field(min_length=1)
    reasons: list[str]


class Alert(Model):
    alert_id: PositiveInt
    grade: Grade
    status: AlertStatus
    room: str = Field(min_length=1)
    roi: str | None = None  # RF만 반응한 L3는 위치를 알 수 없다
    started_at: AwareDatetime
    updated_at: AwareDatetime
    acked_at: AwareDatetime | None = None
    in_cleaning_window: bool
    evidence: list[Evidence] = Field(min_length=1)

    @model_validator(mode="after")
    def check_ack(self) -> Self:
        if (self.status == "acked") != (self.acked_at is not None):
            raise ValueError("acked_at은 확인된 경보에만 있어야 합니다")
        return self


class DeviceStatus(Model):
    device_id: str = Field(min_length=1)
    kind: Source
    online: bool
    last_seen: AwareDatetime | None = None  # 서버 시작 후 아직 수신하지 못했으면 None
    error: str | None = None


# ---------- envelope ----------


class Envelope(Model):
    schema_version: Literal["1.0"] = Field(alias="schema")
    message_id: UUID
    device_id: str = Field(min_length=1)
    sent_at: AwareDatetime


class RadarStatusMessage(Envelope):
    type: Literal["radar.status"]
    payload: RadarStatus


class RadarOccupancyMessage(Envelope):
    type: Literal["radar.occupancy"]
    payload: RadarOccupancy


class RadarDetectionMessage(Envelope):
    type: Literal["radar.detection"]
    payload: RadarDetection


class RfStatusMessage(Envelope):
    type: Literal["rf.status"]
    payload: RfStatus


class RfWindowMessage(Envelope):
    type: Literal["rf.window"]
    payload: RfWindow


class AlertCreatedMessage(Envelope):
    type: Literal["alert.created"]
    payload: Alert


class AlertUpdatedMessage(Envelope):
    type: Literal["alert.updated"]
    payload: Alert


class DeviceStatusMessage(Envelope):
    type: Literal["device.status"]
    payload: DeviceStatus


SensorMessage = Annotated[
    RadarStatusMessage
    | RadarOccupancyMessage
    | RadarDetectionMessage
    | RfStatusMessage
    | RfWindowMessage,
    Field(discriminator="type"),
]

_sensor_message = TypeAdapter(SensorMessage)


def parse_sensor_message(data: dict) -> SensorMessage:
    """센서가 보낸 메시지를 검증한다. 규격에 어긋나면 pydantic.ValidationError가 발생한다."""
    return _sensor_message.validate_python(data)
