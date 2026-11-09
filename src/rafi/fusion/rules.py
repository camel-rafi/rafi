"""융합 규칙. 판단 하나와 미확인 경보 목록을 받아 경보를 어떻게 바꿀지 정한다.

DB를 모르는 순수 함수라서 규칙만 따로 시험할 수 있다.
도착 순서가 아니라 사건 시각으로 비교한다. 레이더는 사건이 끝난 뒤 보내므로
RF가 먼저 도착하는 경우가 흔한데, 순서와 관계없이 같은 등급이 나와야 한다.
"""

from dataclasses import dataclass
from datetime import timedelta

from rafi.common.messages import Detection, Grade


@dataclass(frozen=True)
class OpenAlert:
    """같은 칸의 미확인 경보. 확인된 경보는 묶지 않으므로 여기에 들어오지 않는다."""

    alert_id: int
    grade: Grade
    roi: str | None
    detections: list[Detection]


@dataclass(frozen=True)
class AlertChange:
    alert_id: int | None  # None이면 새 경보
    grade: Grade
    roi: str | None
    detections: list[Detection]  # 이 경보에 새로 연결할 판단


def fuse(
    detection: Detection, open_alerts: list[OpenAlert], window: timedelta
) -> list[AlertChange]:
    if detection.source == "radar":
        return [_fuse_radar(detection, open_alerts, window)]
    return _fuse_rf(detection, open_alerts, window)


def _fuse_radar(
    detection: Detection, open_alerts: list[OpenAlert], window: timedelta
) -> AlertChange:
    same_roi = [
        alert
        for alert in open_alerts
        if alert.roi == detection.roi and _nearby(detection, alert, window, "radar")
    ]
    rf_only = [
        alert
        for alert in open_alerts
        if alert.grade == "L3" and _nearby(detection, alert, window, "rf")
    ]
    # 같은 구역 경보와 L3가 모두 가까우면 L3에만 붙인다. 위치 없는 L3가 남지 않게 하기 위함이다
    target = _latest(rf_only) or _latest(same_roi)

    # RF가 먼저 와서 다른 구역 경보에 이미 붙었어도, 시간창 안이면 이 구역도 L2가 되어야 한다
    rf_evidence: list[Detection] = []
    for alert in open_alerts:
        for rf in _nearby_detections(detection, alert, window, "rf"):
            if rf not in rf_evidence:
                rf_evidence.append(rf)

    if target is None:
        grade: Grade = "L2" if rf_evidence else "L1"
        return AlertChange(None, grade, detection.roi, [detection, *rf_evidence])

    grade = "L2" if rf_evidence or target.grade == "L2" else "L1"
    missing = [rf for rf in rf_evidence if rf not in target.detections]
    return AlertChange(target.alert_id, grade, detection.roi, [detection, *missing])


def _fuse_rf(
    detection: Detection, open_alerts: list[OpenAlert], window: timedelta
) -> list[AlertChange]:
    # RF는 위치를 모르므로 어느 구역인지 고르지 않고 시간창 안의 레이더 경보를 모두 올린다
    radar_alerts = [alert for alert in open_alerts if _nearby(detection, alert, window, "radar")]
    if radar_alerts:
        return [AlertChange(a.alert_id, "L2", a.roi, [detection]) for a in radar_alerts]

    same_device = [
        alert
        for alert in open_alerts
        if alert.grade == "L3"
        and any(
            rf.device == detection.device
            for rf in _nearby_detections(detection, alert, window, "rf")
        )
    ]
    target = _latest(same_device)
    alert_id = target.alert_id if target else None
    return [AlertChange(alert_id, "L3", None, [detection])]


def _nearby(detection: Detection, alert: OpenAlert, window: timedelta, source: str) -> bool:
    return bool(_nearby_detections(detection, alert, window, source))


def _nearby_detections(
    detection: Detection, alert: OpenAlert, window: timedelta, source: str
) -> list[Detection]:
    return [
        other
        for other in alert.detections
        if other.source == source and _gap(detection, other) <= window
    ]


def _gap(a: Detection, b: Detection) -> timedelta:
    """두 사건 구간 사이의 간격. 구간이 겹치면 0 이하가 된다."""
    return max(a.event_start, b.event_start) - min(a.event_end, b.event_end)


def _latest(alerts: list[OpenAlert]) -> OpenAlert | None:
    return max(
        alerts,
        key=lambda alert: max(detection.event_end for detection in alert.detections),
        default=None,
    )
