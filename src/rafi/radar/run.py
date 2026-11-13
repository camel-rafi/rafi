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

화면으로 쓰려면: python -m rafi.radar.gui (같은 탐지 코드, 입력칸과 시작 버튼)

사용 예
  python -m rafi.radar.run --replay ..\sg-radar-tools\recordings\record_1102\danger_tissue_hs_1.pcr --bg-folder ..\sg-radar-tools\recordings\record_1102
  python -m rafi.radar.run --host 127.0.0.1 --bg-folder ..\sg-radar-tools\recordings\record_1102 --jsonl out\rt\messages.jsonl
  python -m rafi.radar.run --host 192.168.30.1 --learn-bg 20 --post http://서버주소:8000/api/v1/messages
"""

from __future__ import annotations

import argparse
import sys
import threading

from .messages import PrintSink
from .session import Settings, run_session


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

    s = Settings(source="replay" if a.replay else "radar", host=a.host or "", port=a.port, replay=a.replay or "",
                 speed=a.speed, stall=a.stall, bg_mode="folder" if a.bg_folder else "learn",
                 learn_s=a.learn_bg or 0.0, bg_folder=a.bg_folder or "", room=a.room, device_id=a.device_id,
                 post=a.post or "", jsonl=a.jsonl or "", out_dir="out/rt")
    printer = PrintSink(quiet=a.quiet)

    def emit(kind, data):
        if kind == "msg":
            printer.send(data)
        elif kind in ("log", "error"):
            print(("[오류] " if kind == "error" else "") + data, file=sys.stderr if kind == "error" else sys.stdout,
                  flush=True)
            if kind == "log" and data == "탐지 시작":
                print("(Ctrl + C로 끝내기)", flush=True)

    stop = threading.Event()
    th = threading.Thread(target=run_session, args=(s, stop, emit), daemon=True)
    th.start()
    try:
        while th.is_alive():
            th.join(0.2)
    except KeyboardInterrupt:
        print("끝내는 중", flush=True)
        stop.set()
        th.join(10)


if __name__ == "__main__":
    main()
