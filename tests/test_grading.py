from datetime import datetime, timedelta

from rafi.common import Detection, Source
from rafi.fusion import Grade, grade_events

T0 = datetime(2026, 10, 20, 14, 0, 0)


def radar(sec: int) -> Detection:
    return Detection(Source.RADAR, T0 + timedelta(seconds=sec), 0.8, roi="vent")


def rf(sec: int) -> Detection:
    return Detection(Source.RF, T0 + timedelta(seconds=sec), 0.7)


def test_radar_only_is_l1():
    alerts = grade_events([radar(0)])
    assert [a.grade for a in alerts] == [Grade.L1]


def test_radar_and_rf_within_window_is_l2():
    alerts = grade_events([radar(0), rf(30)], window=timedelta(seconds=60))
    assert [a.grade for a in alerts] == [Grade.L2]
    assert len(alerts[0].rf) == 1


def test_rf_only_is_l3():
    alerts = grade_events([rf(0)])
    assert [a.grade for a in alerts] == [Grade.L3]


def test_rf_outside_window_stays_separate():
    alerts = grade_events([radar(0), rf(300)], window=timedelta(seconds=60))
    assert [a.grade for a in alerts] == [Grade.L1, Grade.L3]
