"""L1/L2/L3 등급 판단 (계획서 수행 방법 5).

- L1: 레이더만 반응 -> 확인 요청
- L2: 레이더 + RF 반응 -> 우선 확인 요청
- L3: RF만 반응 -> 기존 전파 탐지 수준 알림

레이더 의심 시각 전후 window 안에 들어온 RF 후보를 같은 사건으로 묶는다.
window 기본값은 임시값이며, 카메라 전원 인가 후 통신 시작까지의 지연을 실측해 정한다.
빈 방 보조 단서 가중치는 아직 반영하지 않았다 (이슈에서 구현).
"""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from rafi.common.events import Detection, Source

DEFAULT_WINDOW = timedelta(seconds=120)  # TODO: 실측 후 조정


class Grade(StrEnum):
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


@dataclass(frozen=True)
class Alert:
    grade: Grade
    radar: Detection | None
    rf: tuple[Detection, ...]


def grade_events(
    detections: list[Detection], window: timedelta = DEFAULT_WINDOW
) -> list[Alert]:
    """판단 결과 목록을 사건 단위 경보로 묶는다."""
    radar = sorted((d for d in detections if d.source is Source.RADAR), key=lambda d: d.timestamp)
    rf = sorted((d for d in detections if d.source is Source.RF), key=lambda d: d.timestamp)

    used: set[int] = set()
    alerts: list[Alert] = []
    for r in radar:
        matched_idx = [
            i for i, d in enumerate(rf)
            if i not in used and abs(d.timestamp - r.timestamp) <= window
        ]
        used.update(matched_idx)
        matched = tuple(rf[i] for i in matched_idx)
        alerts.append(Alert(Grade.L2 if matched else Grade.L1, r, matched))

    for i, d in enumerate(rf):
        if i not in used:
            alerts.append(Alert(Grade.L3, None, (d,)))

    return sorted(alerts, key=lambda a: (a.radar or a.rf[0]).timestamp)
