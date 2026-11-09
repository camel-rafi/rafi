"""RF 카메라 후보 판단 (임시 규칙, 중앙 서버에서 실행).

rf.window의 기기별 집계에서 영상 전송처럼 보이는 지속적인 업링크를 찾는다.
규칙을 바꾸면 METHOD의 버전을 올린다. 수치는 시험용 카메라의 실측값으로 조정한다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from rafi.common.messages import Detection, RfWindow

MIN_UPLINK_BYTES_PER_SEC = 20_000
SUSTAIN = timedelta(seconds=15)
MAX_GAP = timedelta(seconds=5)
SCORE = 0.7  # 임시값
METHOD = "rf-rule-v1"


@dataclass
class _Transmission:
    start: datetime
    end: datetime
    bytes_up: int = 0
    rssi_total: float = 0.0
    window_count: int = 0
    active_sec: int = 0  # 공백을 뺀, 임계값을 넘은 시간
    reported: bool = False


class RfJudge:
    """진행 중인 전송을 메모리에 두므로 서버를 재시작하면 지속 시간을 처음부터 다시 센다."""

    def __init__(self) -> None:
        # Pi가 여러 대여도 섞이지 않도록 (장비, 기기 별칭)으로 구분한다
        self._transmissions: dict[tuple[str, str], _Transmission] = {}

    def update(self, device_id: str, window: RfWindow) -> list[Detection]:
        """집계 하나를 반영하고, 이번에 후보로 판단된 전송을 돌려준다."""
        window_end = window.window_start + timedelta(seconds=window.window_sec)
        candidates = []
        for stats in window.devices:
            if stats.bytes_up / window.window_sec < MIN_UPLINK_BYTES_PER_SEC:
                continue

            key = (device_id, stats.device)
            transmission = self._transmissions.get(key)
            if transmission is None or window.window_start - transmission.end > MAX_GAP:
                transmission = _Transmission(start=window.window_start, end=window_end)
                self._transmissions[key] = transmission

            transmission.end = max(transmission.end, window_end)
            transmission.bytes_up += stats.bytes_up
            transmission.rssi_total += stats.rssi_mean
            transmission.window_count += 1
            transmission.active_sec += window.window_sec

            if not transmission.reported and transmission.end - transmission.start >= SUSTAIN:
                transmission.reported = True  # 한 번 이어지는 전송에서 후보는 한 번만 만든다
                candidates.append(_candidate(stats.device, transmission))
        return candidates


def _candidate(device: str, transmission: _Transmission) -> Detection:
    duration = (transmission.end - transmission.start).total_seconds()
    rate = transmission.bytes_up / transmission.active_sec
    return Detection(
        source="rf",
        event_start=transmission.start,
        event_end=transmission.end,
        device=device,
        rssi_mean=transmission.rssi_total / transmission.window_count,
        score=SCORE,
        method=METHOD,
        reasons=[f"업링크 평균 {rate:,.0f} B/s, {duration:.0f}초 지속"],
    )
