r"""RaFi 실시간 레이더 탐지기 실행 (레이더 PC에서)

입력 (하나 고르기)
  --host 192.168.30.1        실제 레이더 (기본 IP는 교육안 기준 192.168.30.1)
  --host 127.0.0.1           같은 PC에서 Radar Emulator로 녹화를 재생할 때
  --replay 파일.pcr          에뮬레이터 없이 녹화를 직접 재생 (--speed 1 = 실제 속도, 0 = 최대한 빠르게)

배경 (빈 칸에 늘 찍히는 벽·비품 반사 빼기, 하나 고르기)
  --bg-folder 녹화폴더       그 폴더의 빈 칸 녹화(stall json의 empty_files)로 만듦 (11-02 칸이면 이것)
  --learn-bg 20              시작 후 20초 동안 칸이 비어 있다고 보고 그때 배경을 배움 (새 칸에 설치할 때)

출력
  화면에 경보 한 줄씩 (항상), --jsonl 파일에 모든 메시지, --post 주소로 HTTP POST (서버가 정해지면)

사용 예
  python -m rafi.radar.run --replay ..\sg-radar-tools\recordings\record_1102\danger_tissue_hs_1.pcr --bg-folder ..\sg-radar-tools\recordings\record_1102
  python -m rafi.radar.run --host 127.0.0.1 --bg-folder ..\sg-radar-tools\recordings\record_1102 --jsonl out\rt\messages.jsonl
  python -m rafi.radar.run --host 192.168.30.1 --learn-bg 20 --post http://서버주소:8000/api/v1/messages
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from .analysis_common import background_codes, prefix_trim
from .detector import Detector, learn_background
from .messages import KST, HttpSink, JsonlSink, PrintSink, envelope
from .stream import ReplaySource, TcpSource


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--host", help="레이더 또는 에뮬레이터 주소")
    src.add_argument("--replay", help=".pcr 녹화 파일")
    ap.add_argument("--port", type=int, default=29172)
    ap.add_argument("--speed", type=float, default=1.0, help="--replay 재생 속도 (0 = 기다리지 않음)")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    bg = ap.add_mutually_exclusive_group(required=True)
    bg.add_argument("--bg-folder", help="빈 칸 녹화가 있는 폴더")
    bg.add_argument("--learn-bg", type=float, help="시작 후 이 초 동안 빈 칸으로 보고 배경을 배움")
    ap.add_argument("--room", default="stall-1")
    ap.add_argument("--device-id", default="radar-1")
    ap.add_argument("--jsonl", default=None, help="모든 메시지를 저장할 파일")
    ap.add_argument("--post", default=None, help="서버 주소 (POST, JSON). 예: http://서버주소:8000/api/v1/messages")
    ap.add_argument("--quiet", action="store_true", help="화면에는 경보만")
    a = ap.parse_args()

    skip = prefix_trim(a.replay, "out/rt")["frames"] if a.replay else 0
    source = ReplaySource(a.replay, a.speed, skip) if a.replay else TcpSource(a.host, a.port)
    frames = iter(source)

    if a.bg_folder:
        bg_codes = background_codes(a.stall, a.bg_folder, "out/rt")
        print(f"배경 칸 {len(bg_codes)}개 (빈 칸 녹화에서)", flush=True)
    else:
        print(f"배경 배우는 중: {a.learn_bg:.0f}초 동안 칸을 비워 두세요")
        bg_codes = learn_background(frames, a.learn_bg)
        print(f"배경 칸 {len(bg_codes)}개 배움")

    sinks = [PrintSink(quiet=a.quiet)]
    if a.jsonl:
        sinks.append(JsonlSink(a.jsonl))
    if a.post:
        http = HttpSink(a.post)
        sinks.append(http)
    send = lambda m: [s.send(m) for s in sinks]  # noqa: E731

    clock = None  # 실시간: 기본 시계
    if a.replay:  # 재생: 녹화 시각을 '지금'부터 흐르게 (speed가 1이 아니면 사건 시각도 녹화 기준 초로 계산됨)
        from .detector import sim_clock
        from .radar_io import iter_frames
        t_first = next(iter(iter_frames(a.replay)))[2]
        clock = sim_clock(datetime.now(KST) - timedelta(seconds=t_first))
    det = Detector(a.stall, bg_codes, room=a.room, device_id=a.device_id, clock=clock)
    t_occ = t_stat = None
    n_frames, t_rate = 0, time.monotonic()
    print("탐지 시작 (Ctrl + C로 끝내기)", flush=True)
    try:
        for t, pts in frames:
            now = time.monotonic()
            if pts is None:  # 레이더 연결 끊김: 열린 사건을 닫고 상태를 바로 알림
                for m in det.close_all():
                    send(m)
                send(envelope("radar.status", a.device_id, {"sensor_ok": False, "fps": 0.0,
                                                            "error": source.last_error or "레이더 연결 끊김"},
                              datetime.now(KST)))
                t_stat = now
                continue
            if pts is not None:
                for m in det.step(t, pts):
                    send(m)
                n_frames += 1
                if t_occ is None or t - t_occ >= 1.0:  # radar.occupancy 1초마다
                    t_occ = t
                    send(det.occupancy())
            if t_stat is None or now - t_stat >= 10.0:  # radar.status 10초마다
                fps = n_frames / max(now - t_rate, 1e-6)
                n_frames, t_rate, t_stat = 0, now, now
                # 서버 규격: sensor_ok(센서 데이터를 받고 있나), fps, error(문자열 하나 또는 null)
                error = source.last_error if not source.connected else None
                send(envelope("radar.status", a.device_id,
                              {"sensor_ok": bool(source.connected), "fps": round(fps, 1), "error": error or None},
                              datetime.now(KST)))
    except KeyboardInterrupt:
        print("끝냄")
    for m in det.close_all():  # 재생이 끝났거나 Ctrl + C
        send(m)


if __name__ == "__main__":
    main()