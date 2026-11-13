"""시나리오와 시뮬레이터.

시나리오는 "몇 초에 무엇을 하는가"의 목록이다. 시뮬레이터는 1초씩 진행하면서
실제 모듈처럼 상태 메시지(10초)와 rf.window(1초)를 함께 보낸다.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from rafi.common.messages import (
    SCHEMA_VERSION,
    Envelope,
    RadarDetection,
    RadarDetectionMessage,
    RadarStatus,
    RadarStatusMessage,
    RfDeviceStats,
    RfStatus,
    RfStatusMessage,
    RfWindow,
    RfWindowMessage,
)
from rafi.sim.sender import Sender

RADAR_ID = "radar-1"
PI_ID = "pi-1"
CAMERA = "cam-01"
STATUS_INTERVAL_SEC = 10
IDLE_UPLINK = 500  # 바이트/초. RF 판단 임계값(20,000)보다 훨씬 적은 평소 트래픽
CAMERA_UPLINK = 60_000  # 바이트/초. 영상 전송
QUIET_GAP_SEC = 130  # demo에서 시나리오끼리 융합 시간창(120초) 밖으로 떨어뜨린다
METHOD = "fake-v1"


class Clock:
    """시뮬레이션 시각. 실제 속도면 실제로 기다리고, 빠른 모드면 시각만 넘긴다."""

    def __init__(self, start: datetime, realtime: bool) -> None:
        self._now = start
        self.realtime = realtime

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        if self.realtime:
            time.sleep(seconds)
        self._now += timedelta(seconds=seconds)


Action = Callable[["Simulator"], None]


@dataclass
class Scenario:
    name: str
    description: str
    expected: str
    duration_sec: int
    steps: list[tuple[int, Action]] = field(default_factory=list)
    rf_traffic: bool = True  # False면 rf.window 없이 상태 메시지만 보낸다


class Simulator:
    def __init__(self, sender: Sender, clock: Clock, log: Callable[[str], None] = print) -> None:
        self.sender = sender
        self.clock = clock
        self.log = log
        self.camera_on = False
        self.radar_state: Literal["ok", "fail", "silent"] = "ok"

    def run(self, scenario: Scenario) -> None:
        self.log(f"=== {scenario.name}: {scenario.description} (예상: {scenario.expected})")
        self.camera_on = False
        self.radar_state = "ok"  # 시나리오를 시작할 때마다 장비를 정상으로 돌린다
        for second in range(scenario.duration_sec + 1):
            if second > 0 and scenario.rf_traffic:
                self.send_rf_window()
            for at, action in scenario.steps:
                if at == second:
                    action(self)
            if second % STATUS_INTERVAL_SEC == 0:
                self.send_statuses()
            if second < scenario.duration_sec:
                self.clock.sleep(1)

    # ---------- 주기 메시지 ----------

    def send_statuses(self) -> None:
        if self.radar_state != "silent":
            self.send_radar_status()
        payload = RfStatus(sensor_ok=True, channel=6, dropped_frames=0)
        self._send(RfStatusMessage(type="rf.status", payload=payload, **self._envelope(PI_ID)))

    def send_radar_status(self) -> None:
        if self.radar_state == "ok":
            payload = RadarStatus(sensor_ok=True, fps=20.0)
        else:
            payload = RadarStatus(sensor_ok=False, fps=0.0, error="프레임 수신 없음 (가짜 송신기)")
        self._send(RadarStatusMessage(type="radar.status", payload=payload, **self._envelope(RADAR_ID)))

    def send_rf_window(self) -> None:
        uplink = CAMERA_UPLINK if self.camera_on else IDLE_UPLINK
        stats = RfDeviceStats(
            device=CAMERA,
            frames_up=max(1, uplink // 1400),
            bytes_up=uplink,
            frames_down=2,
            bytes_down=200,
            size_p50=1400 if self.camera_on else 250,
            size_p90=1500 if self.camera_on else 400,
            rssi_mean=-48,
        )
        window = RfWindow(
            window_start=self.clock.now() - timedelta(seconds=1), window_sec=1, devices=[stats]
        )
        self._send(RfWindowMessage(type="rf.window", payload=window, **self._envelope(PI_ID)))

    # ---------- 시나리오 동작 ----------

    def radar_detection(self, roi: str, seconds: int) -> None:
        """방금 끝난 사건을 보낸다. 레이더는 사건이 끝난 뒤 판단을 보낸다."""
        end = self.clock.now()
        payload = RadarDetection(
            source="radar",
            event_start=end - timedelta(seconds=seconds),
            event_end=end,
            roi=roi,
            score=0.8,
            method=METHOD,
            reasons=[f"{roi}에 손 머묾 {seconds}초 (가짜 송신기)"],
        )
        message = RadarDetectionMessage(
            type="radar.detection", payload=payload, **self._envelope(RADAR_ID)
        )
        result = self._send(message)
        self.log(f"[{self._time()}] 레이더 판단 {roi} {seconds}초 → {result}")

    def set_camera(self, on: bool) -> None:
        self.camera_on = on
        self.log(f"[{self._time()}] 카메라 전송 {'시작' if on else '끝'}")

    def set_radar(self, state: Literal["ok", "fail", "silent"]) -> None:
        self.radar_state = state
        labels = {"ok": "정상", "fail": "센서 멈춤", "silent": "상태 전송 중단"}
        self.log(f"[{self._time()}] 레이더 {labels[state]}")
        if state != "silent":
            self.send_radar_status()  # 실제 모듈도 상태가 바뀌면 바로 알린다

    # ---------- 공통 ----------

    def _send(self, message: Envelope) -> str:
        result = self.sender.send(message)
        if result == "failed":
            self.log(f"[{self._time()}] {message.type} 전송 실패 (서버가 켜져 있는지 확인)")
        return result

    def _envelope(self, device_id: str) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "message_id": uuid4(),
            "device_id": device_id,
            "sent_at": self.clock.now(),
        }

    def _time(self) -> str:
        return self.clock.now().strftime("%H:%M:%S")


def detection(roi: str, seconds: int = 10) -> Action:
    return lambda sim: sim.radar_detection(roi, seconds)


def camera(on: bool) -> Action:
    return lambda sim: sim.set_camera(on)


def radar(state: Literal["ok", "fail", "silent"]) -> Action:
    return lambda sim: sim.set_radar(state)


SCENARIOS = {
    scenario.name: scenario
    for scenario in [
        Scenario(
            "radar-only",
            "레이더 판단 1개",
            "L1",
            duration_sec=3,
            steps=[(2, detection("dispenser_top"))],
        ),
        Scenario(
            "rf-only",
            "카메라 전송 18초",
            "L3",
            duration_sec=22,
            steps=[(2, camera(True)), (20, camera(False))],
        ),
        Scenario(
            "radar-then-rf",
            "레이더 판단 뒤 카메라 전송",
            "L1 → L2",
            duration_sec=25,
            steps=[(2, detection("dispenser_top")), (5, camera(True)), (23, camera(False))],
        ),
        Scenario(
            "rf-then-radar",
            "카메라 전송 뒤 레이더 판단",
            "L3 → L2",
            duration_sec=25,
            steps=[(1, camera(True)), (20, detection("dispenser_top")), (23, camera(False))],
        ),
        Scenario(
            "two-zones",
            "두 구역 레이더 판단 뒤 카메라 전송",
            "두 경보 모두 L2",
            duration_sec=25,
            steps=[
                (2, detection("dispenser_top")),
                (3, detection("sanbox_top")),
                (4, camera(True)),
                (23, camera(False)),
            ],
        ),
        Scenario(
            "sensor-fail",
            "레이더 센서 멈춤",
            "레이더 끊김, 모듈이 보낸 오류 표시",
            duration_sec=5,
            steps=[(2, radar("fail"))],
        ),
        Scenario(
            "device-down",
            "레이더 상태 전송 중단",
            "약 30초 뒤 레이더 끊김, '상태 메시지 수신 없음' 표시",
            duration_sec=35,
            steps=[(1, radar("silent"))],
        ),
    ]
}

# 대기 중에는 판단에 영향이 없는 평소 RF 트래픽을 생략해 전송량을 줄인다
QUIET = Scenario(
    "quiet", "다음 시나리오와 섞이지 않도록 대기", "변화 없음", QUIET_GAP_SEC, rf_traffic=False
)


def demo_sequence() -> list[Scenario]:
    """모든 시나리오를 차례로. 시나리오 사이에 대기를 넣어 서로 묶이지 않게 한다."""
    sequence: list[Scenario] = []
    for scenario in SCENARIOS.values():
        if sequence:
            sequence.append(QUIET)
        sequence.append(scenario)
    return sequence
