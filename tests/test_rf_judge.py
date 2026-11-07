from datetime import UTC, datetime, timedelta

from rafi.common.messages import RfDeviceStats, RfWindow
from rafi.rf.judge import METHOD, RfJudge

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
ENOUGH = 20_000


def window(second: int, bytes_up: int = ENOUGH, device: str = "cam-01", sec: int = 1) -> RfWindow:
    stats = RfDeviceStats(
        device=device,
        frames_up=10,
        bytes_up=bytes_up,
        frames_down=1,
        bytes_down=100,
        size_p50=1000,
        size_p90=1400,
        rssi_mean=-50,
    )
    return RfWindow(
        window_start=T0 + timedelta(seconds=second), window_sec=sec, devices=[stats]
    )


def feed(judge: RfJudge, seconds, **kwargs) -> list:
    results = []
    for second in seconds:
        results += judge.update("pi-1", window(second, **kwargs))
    return results


def test_candidate_after_sustained_uplink():
    judge = RfJudge()
    assert feed(judge, range(14)) == []

    candidates = feed(judge, [14])

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.source == "rf"
    assert candidate.device == "cam-01"
    assert candidate.event_start == T0
    assert candidate.event_end == T0 + timedelta(seconds=15)
    assert candidate.method == METHOD
    assert candidate.rssi_mean == -50


def test_only_one_candidate_per_transmission():
    assert len(feed(RfJudge(), range(60))) == 1


def test_uplink_below_threshold_is_ignored():
    assert feed(RfJudge(), range(60), bytes_up=ENOUGH - 1) == []


def test_threshold_uses_rate_not_window_total():
    judge = RfJudge()
    low_rate = [window(second, bytes_up=ENOUGH, sec=2) for second in range(0, 60, 2)]
    assert [c for w in low_rate for c in judge.update("pi-1", w)] == []


def test_gap_at_limit_keeps_same_transmission():
    candidates = feed(RfJudge(), [*range(10), *range(15, 20)])  # 10초에 끊겨 15초에 재개
    assert len(candidates) == 1
    assert candidates[0].event_start == T0


def test_reason_rate_excludes_gap():
    candidates = feed(RfJudge(), [*range(10), 15], bytes_up=30_000)
    assert "30,000 B/s" in candidates[0].reasons[0]


def test_gap_over_limit_restarts_count():
    judge = RfJudge()
    assert feed(judge, [*range(10), *range(16, 30)]) == []  # 공백 6초, 재시작 후 14초

    candidates = feed(judge, [30])

    assert len(candidates) == 1
    assert candidates[0].event_start == T0 + timedelta(seconds=16)


def test_new_transmission_after_gap_makes_new_candidate():
    assert len(feed(RfJudge(), [*range(15), *range(30, 45)])) == 2


def test_devices_are_counted_separately():
    judge = RfJudge()
    feed(judge, range(10), device="cam-01")
    assert feed(judge, range(10, 15), device="cam-02") == []


def test_same_alias_on_different_pi_is_counted_separately():
    judge = RfJudge()
    for second in range(10):
        judge.update("pi-1", window(second))
    assert [c for s in range(10, 15) for c in judge.update("pi-2", window(s))] == []
