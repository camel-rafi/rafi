"""프레임 입력: 실제 레이더·Radar Emulator(TCP 29172) 또는 .pcr 녹화 재생.

둘 다 같은 모양으로 내보낸다: (t 초, 점 N×6 float32 [x, y, z, doppler, power, targetId])
  - t: 실시간이면 time.monotonic(), 재생이면 녹화 안의 시각
  - 좌표는 레이더 좌표 (녹화 파일과 같음)

패킷 구조 (radar-studio/src/device/retina.cpp, radar-emulator/src/device/mock_protocol.cpp 기준)
  패킷 머리 36바이트: uint32 × 9 = [예약, 0xABCD4321, 예약, 예약, 본문 크기, 예약 × 4]
  본문: 프레임 머리 16바이트 [uint64 0x0807060504030201, uint32 프레임 번호, uint32 점 개수 N]
        점 N개 × [x, y, z, doppler, power] float32, 이어서 점별 targetId int32 × N, (있으면) 추적 대상 구역
  접속하면 바로 보내 준다 (보낼 명령 없음)
"""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np

PACKET_MAGIC = struct.pack("<I", 0xABCD4321)
FRAME_MAGIC = 0x0807060504030201
HEADER = 36


def parse_packet(pkt: bytes) -> tuple[int, np.ndarray]:
    """패킷 하나 → (프레임 번호, 점 N×6)."""
    magic, count, n = struct.unpack_from("<QII", pkt, HEADER)
    if magic != FRAME_MAGIC:
        raise ValueError(f"프레임 머리 값이 이상함: {magic:#x}")
    off = HEADER + 16
    xyzdp = np.frombuffer(pkt, dtype="<f4", count=5 * n, offset=off).reshape(n, 5)
    ids = np.full(n, -1, dtype=np.float32)
    off2 = off + 20 * n
    if len(pkt) >= off2 + 4 * n:
        ids = np.frombuffer(pkt, dtype="<i4", count=n, offset=off2).astype(np.float32)
    return count, np.column_stack([xyzdp, ids]).astype(np.float32)


class TcpSource:
    """레이더(또는 에뮬레이터)에 TCP로 붙어서 프레임을 받는다. 끊기면 다시 붙는다.

    status 속성: connected, frames, last_error (radar.status 메시지용)
    """

    def __init__(self, host: str, port: int = 29172, retry_s: float = 2.0, timeout_s: float = 5.0):
        self.host, self.port, self.retry_s, self.timeout_s = host, port, retry_s, timeout_s
        self.connected = False
        self.frames = 0
        self.last_error = ""

    def __iter__(self) -> Iterator[tuple[float, np.ndarray]]:
        while True:
            try:
                with socket.create_connection((self.host, self.port), timeout=self.timeout_s) as s:
                    s.settimeout(self.timeout_s)
                    self.connected, self.last_error = True, ""
                    buf = bytearray()
                    while True:
                        chunk = s.recv(65536)
                        if not chunk:
                            raise ConnectionError("레이더가 연결을 끊음")
                        buf += chunk
                        while True:
                            i = buf.find(PACKET_MAGIC)
                            if i < 0:
                                del buf[:-3]
                                break
                            if i < 4:  # 머리 첫 4바이트(예약)가 아직 없음
                                del buf[:i + 1]
                                continue
                            start = i - 4
                            if len(buf) < start + HEADER:
                                del buf[:start]
                                break
                            size = struct.unpack_from("<I", buf, start + 16)[0]
                            if len(buf) < start + HEADER + size:
                                del buf[:start]
                                break
                            pkt = bytes(buf[start:start + HEADER + size])
                            del buf[:start + HEADER + size]
                            try:
                                _, pts = parse_packet(pkt)
                            except (ValueError, struct.error) as e:
                                self.last_error = str(e)
                                continue
                            self.frames += 1
                            yield time.monotonic(), pts
            except OSError as e:  # 접속 실패, 끊김, 시간 초과
                self.connected = False
                self.last_error = f"{type(e).__name__}: {e}"
                yield time.monotonic(), None  # 끊긴 것을 알림 (상태 메시지용)
                time.sleep(self.retry_s)


class ReplaySource:
    """.pcr 녹화를 재생. speed=1이면 실제 속도, 0이면 기다리지 않고 최대한 빠르게 (평가용).
    skip_frames: 앞에서 버릴 프레임 수 (녹화 앞에 다른 녹화가 붙은 경우)"""

    def __init__(self, pcr: str | Path, speed: float = 1.0, skip_frames: int = 0):
        self.pcr, self.speed, self.skip = Path(pcr), speed, skip_frames
        self.connected = True
        self.frames = 0
        self.last_error = ""

    def __iter__(self) -> Iterator[tuple[float, np.ndarray]]:
        from .radar_io import iter_frames

        wall0 = t0 = None
        for i, (_, _, t, pts) in enumerate(iter_frames(str(self.pcr))):
            if i < self.skip:
                continue
            if t0 is None:
                wall0, t0 = time.monotonic(), t
            if self.speed > 0:
                wait = wall0 + (t - t0) / self.speed - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
            self.frames += 1
            yield float(t), np.asarray(pts, dtype=np.float32)
