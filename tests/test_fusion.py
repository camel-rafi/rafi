from datetime import UTC, datetime, timedelta
from itertools import permutations

import pytest

from rafi.common.messages import Detection
from rafi.fusion.rules import AlertChange, OpenAlert, fuse

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
WINDOW = timedelta(seconds=120)


def radar(roi: str = "vent", start: int = 0, end: int = 10) -> Detection:
    return Detection(
        source="radar",
        event_start=T0 + timedelta(seconds=start),
        event_end=T0 + timedelta(seconds=end),
        roi=roi,
        score=0.8,
        method="radar-v1",
        reasons=[],
    )


def rf(device: str = "cam-01", start: int = 0, end: int = 15) -> Detection:
    return Detection(
        source="rf",
        event_start=T0 + timedelta(seconds=start),
        event_end=T0 + timedelta(seconds=end),
        device=device,
        score=0.7,
        method="rf-v1",
        reasons=[],
    )


def alert(alert_id: int, grade: str, roi: str | None, *detections: Detection) -> OpenAlert:
    return OpenAlert(alert_id, grade, roi, list(detections))


def test_radar_without_target_creates_l1():
    detection = radar()
    assert fuse(detection, [], WINDOW) == [AlertChange(None, "L1", "vent", [detection])]


def test_radar_same_roi_is_added_to_existing_alert():
    first, second = radar(), radar(start=20, end=30)
    changes = fuse(second, [alert(1, "L1", "vent", first)], WINDOW)
    assert changes == [AlertChange(1, "L1", "vent", [second])]


def test_radar_same_roi_keeps_l2():
    existing = alert(1, "L2", "vent", radar(), rf())
    second = radar(start=20, end=30)
    assert fuse(second, [existing], WINDOW)[0].grade == "L2"


def test_radar_different_roi_creates_separate_alert():
    changes = fuse(radar("door"), [alert(1, "L1", "vent", radar())], WINDOW)
    assert changes[0].alert_id is None
    assert changes[0].roi == "door"
    assert changes[0].grade == "L1"


def test_radar_promotes_l3_and_sets_roi():
    detection = radar(start=20, end=30)
    changes = fuse(detection, [alert(1, "L3", None, rf())], WINDOW)
    assert changes == [AlertChange(1, "L2", "vent", [detection])]


def test_radar_promotes_most_recent_l3():
    old = alert(1, "L3", None, rf("cam-01", 0, 15))
    recent = alert(2, "L3", None, rf("cam-02", 30, 45))
    assert fuse(radar(start=50, end=60), [old, recent], WINDOW)[0].alert_id == 2


def test_radar_near_same_roi_and_l3_changes_only_l3():
    # 첫 판단과 RF는 130초 떨어져 따로 경보가 됐고, 늦게 도착한 판단이 둘 다에 가깝다
    detection = radar(start=100, end=135)
    alerts = [alert(1, "L1", "vent", radar()), alert(2, "L3", None, rf(start=140, end=155))]
    assert fuse(detection, alerts, WINDOW) == [AlertChange(2, "L2", "vent", [detection])]


def test_radar_same_roi_is_promoted_by_rf_on_other_alert():
    candidate = rf(start=140, end=155)
    detection = radar(start=100, end=135)
    alerts = [
        alert(1, "L1", "vent", radar()),
        alert(2, "L2", "door", radar("door", 100, 110), candidate),
    ]
    assert fuse(detection, alerts, WINDOW) == [
        AlertChange(1, "L2", "vent", [detection, candidate])
    ]


def test_new_radar_alert_starts_l2_and_links_nearby_rf():
    candidate = rf()
    detection = radar("door", start=20, end=30)
    existing = alert(1, "L2", "vent", candidate, radar())
    assert fuse(detection, [existing], WINDOW) == [
        AlertChange(None, "L2", "door", [detection, candidate])
    ]


def test_rf_without_target_creates_l3():
    detection = rf()
    assert fuse(detection, [], WINDOW) == [AlertChange(None, "L3", None, [detection])]


def test_rf_promotes_all_nearby_radar_alerts():
    candidate = rf(start=20, end=35)
    alerts = [alert(1, "L1", "vent", radar("vent")), alert(2, "L1", "door", radar("door"))]
    assert fuse(candidate, alerts, WINDOW) == [
        AlertChange(1, "L2", "vent", [candidate]),
        AlertChange(2, "L2", "door", [candidate]),
    ]


def test_rf_adds_evidence_to_l2():
    candidate = rf(start=20, end=35)
    existing = alert(1, "L2", "vent", radar(), rf("cam-02"))
    assert fuse(candidate, [existing], WINDOW) == [AlertChange(1, "L2", "vent", [candidate])]


def test_rf_same_device_is_added_to_l3():
    candidate = rf(start=40, end=55)
    assert fuse(candidate, [alert(1, "L3", None, rf())], WINDOW) == [
        AlertChange(1, "L3", None, [candidate])
    ]


def test_rf_other_device_creates_separate_l3():
    changes = fuse(rf("cam-02", 40, 55), [alert(1, "L3", None, rf())], WINDOW)
    assert changes[0].alert_id is None
    assert changes[0].grade == "L3"


def test_gap_exactly_at_window_is_grouped():
    changes = fuse(rf(start=130, end=145), [alert(1, "L1", "vent", radar())], WINDOW)
    assert changes[0].alert_id == 1


def test_gap_over_window_is_not_grouped():
    changes = fuse(rf(start=131, end=146), [alert(1, "L1", "vent", radar())], WINDOW)
    assert changes[0].alert_id is None
    assert changes[0].grade == "L3"


def test_overlapping_events_are_grouped():
    changes = fuse(rf(start=5, end=500), [alert(1, "L1", "vent", radar())], WINDOW)
    assert changes[0].grade == "L2"


# ---------- 도착 순서와 관계없이 같은 결과 ----------


def run(detections: tuple[Detection, ...]) -> list[OpenAlert]:
    """fuse의 결과를 저장소처럼 반영하며 판단을 순서대로 넣는다."""
    alerts: list[OpenAlert] = []
    for detection in detections:
        for change in fuse(detection, alerts, WINDOW):
            if change.alert_id is None:
                alerts.append(
                    alert(len(alerts) + 1, change.grade, change.roi, *change.detections)
                )
            else:
                index = change.alert_id - 1
                alerts[index] = alert(
                    change.alert_id,
                    change.grade,
                    change.roi,
                    *alerts[index].detections,
                    *change.detections,
                )
    return alerts


@pytest.mark.parametrize(
    "order", list(permutations([radar("vent"), radar("door", 5, 12), rf(start=3, end=18)]))
)
def test_every_arrival_order_gives_l2_for_both_zones_with_rf_evidence(order):
    alerts = run(order)
    assert sorted(a.roi for a in alerts) == ["door", "vent"]
    assert {a.grade for a in alerts} == {"L2"}
    assert all(any(d.source == "rf" for d in a.detections) for a in alerts)
