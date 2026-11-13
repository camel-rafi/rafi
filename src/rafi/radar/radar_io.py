"""RaFi 레이더 처리 - ① 입력 어댑터

.pcr 파일을 읽어서 프레임마다 (시각[초], 포인트 배열)로 바꿔 준다.
포인트 배열은 N행 6열: [x, y, z, doppler, power, target_id]

이후 단계(배경 맵, 접촉 판정 ...)는 이 파일의 iter_frames만 사용한다.
나중에 실시간 센서 입력으로 바꿀 때도 같은 형태로 내보내는 함수만 하나 더 만들면 된다.

사용 예:
    python -m rafi.radar.radar_io path/to/file.pcr
"""

from __future__ import annotations

# sg-radar-tools/tools/pcr 경로를 찾아서 pcr_loader를 가져온다.
# 기본값: rafi 저장소 폴더와 sg-radar-tools 폴더가 나란히 있는 경우
#   (작업폴더/rafi/src/rafi/radar/radar_io.py → 작업폴더/sg-radar-tools).
# 다른 곳에 있으면 환경변수 SG_RADAR_TOOLS에 sg-radar-tools 경로를 넣는다.
import os
import sys
from pathlib import Path

import numpy as np

SG_RADAR_TOOLS = Path(
    os.environ.get("SG_RADAR_TOOLS", Path(__file__).resolve().parents[4] / "sg-radar-tools")
)
sys.path.insert(0, str(SG_RADAR_TOOLS / "tools" / "pcr"))

from pcr_loader import load  # noqa: E402

COLS = ["x", "y", "z", "doppler", "power", "target_id"]


def frame_to_array(frame) -> np.ndarray:
    """프레임 하나의 포인트 목록을 (N, 6) numpy 배열로 변환."""
    if not frame.points:
        return np.zeros((0, 6), dtype=np.float32)
    return np.array(
        [(p.x, p.y, p.z, p.doppler, p.power, p.target_id) for p in frame.points],
        dtype=np.float32,
    )


def iter_frames(pcr_path, session_index: int | None = None):
    """(세션 이름, 프레임 번호, 시각[초], 포인트 배열)을 하나씩 내보낸다.

    - 시각: 세션 시작부터 누적한 시간. deltaUs가 '직전 프레임과의 간격'이라 누적해서 만든다.
    - session_index를 주면 그 세션만, 안 주면 모든 세션을 차례대로.
    """
    rec = load(pcr_path)
    sessions = rec.sessions if session_index is None else [rec.sessions[session_index]]
    for s in sessions:
        t_us = 0
        for i, f in enumerate(s.frames):
            t_us += f.delta_us
            yield s.name, i, t_us / 1e6, frame_to_array(f)


def summarize(pcr_path) -> None:
    """1번 점검용 요약: 센서 설정, 세션별 프레임 수·길이·포인트 수·북마크."""
    rec = load(pcr_path)
    spec = rec.header.sensor_spec
    print(f"파일: {pcr_path}")
    print(f"센서 위치: ({spec.pos_x:.2f}, {spec.pos_y:.2f}, {spec.pos_z:.2f}) m, "
          f"yaw {spec.yaw_deg:.1f}°, pitch {spec.pitch_deg:.1f}°")
    print(f"시야각: 가로 {spec.hfov_deg:.1f}°, 세로 {spec.vfov_deg:.1f}°")
    print(f"측정 범위: x {spec.range_min_x:.2f}~{spec.range_max_x:.2f}, "
          f"y {spec.range_min_y:.2f}~{spec.range_max_y:.2f}, "
          f"z {spec.range_min_z:.2f}~{spec.range_max_z:.2f} m\n")

    for s in rec.sessions:
        n_pts = np.array([len(f.points) for f in s.frames])
        dur = sum(f.delta_us for f in s.frames) / 1e6
        fps = len(s.frames) / dur if dur > 0 else float("nan")
        print(f"[세션 {s.id}] '{s.name}'  프레임 {len(s.frames)}개, {dur:.1f}초 (약 {fps:.1f} fps)")
        if len(n_pts):
            print(f"  프레임당 포인트 수: 평균 {n_pts.mean():.0f}, 최소 {n_pts.min()}, 최대 {n_pts.max()}")
        for b in s.bookmarks:
            print(f"  북마크: 프레임 {b.frame_index} - {b.description}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python -m rafi.radar.radar_io path/to/file.pcr")
        raise SystemExit(1)
    summarize(sys.argv[1])
