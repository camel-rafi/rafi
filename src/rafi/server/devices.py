"""장비별 연결 상태 관리.

상태는 메모리에만 두므로 서버를 재시작하면 모든 장비가 끊김으로 시작한다.
연결 판단에는 서버 시각만 쓴다. 장비 시계가 틀어져도 판단이 흔들리지 않게 하기 위함이다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from rafi.common.messages import DeviceStatus, Source
from rafi.server.config import DeviceConfig

NO_STATUS_ERROR = "상태 메시지 수신 없음"


@dataclass
class _DeviceState:
    kind: Source
    last_seen: datetime | None = None
    sensor_ok: bool = False
    error: str | None = None


class DeviceTracker:
    def __init__(self, devices: dict[str, DeviceConfig], timeout: timedelta) -> None:
        self._timeout = timeout
        self._states = {
            device_id: _DeviceState(kind=device.kind) for device_id, device in devices.items()
        }

    def update(self, device_id: str, sensor_ok: bool, error: str | None, now: datetime) -> None:
        """상태 메시지를 받았을 때 호출한다."""
        state = self._states[device_id]
        state.last_seen = now
        state.sensor_ok = sensor_ok
        state.error = error

    def statuses(self, now: datetime) -> list[DeviceStatus]:
        return [
            self._status(device_id, state, now) for device_id, state in self._states.items()
        ]

    def _status(self, device_id: str, state: _DeviceState, now: datetime) -> DeviceStatus:
        receiving = state.last_seen is not None and now - state.last_seen < self._timeout
        return DeviceStatus(
            device_id=device_id,
            kind=state.kind,
            online=receiving and state.sensor_ok,
            last_seen=state.last_seen,
            error=state.error if receiving else NO_STATUS_ERROR,
        )
