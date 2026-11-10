"""실시간 탐지 한 번 돌리기 (명령줄 run.py와 화면 gui.py가 같이 쓴다).

  settings = Settings(...)            # 입력·칸·배경·출력 설정
  run_session(settings, stop, emit)   # stop(threading.Event)이 켜질 때까지 돈다

emit(kind, data)로 바깥에 알린다.
  ("log", 문자열)      진행 안내 (배경 배우는 중, 연결 끊김 등)
  ("msg", 메시지 dict) 탐지기가 만든 메시지 (radar.detection / occupancy / status), 내부 칸 포함
  ("stats", dict)      1초마다: connected, fps, frames, sent_ok, sent_fail, send_error
  ("done", None)       끝남
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]  # 저장소 폴더 (src/rafi/radar/session.py 기준)

# 칸 설정 파일에서 화면으로 꺼내 고칠 수 있는 값 (설치할 때 재는 값)
POSE_KEYS = ("width", "depth", "height", "radar_pos", "yaw_deg", "pitch_deg", "roll_deg", "mirror_x")


@dataclass
class Settings:
    source: str = "radar"  # radar(실제 레이더) / emulator(같은 PC 에뮬레이터) / replay(녹화 파일 직접)
    host: str = "192.168.30.1"
    port: int = 29172
    replay: str = ""
    speed: float = 1.0
    stall: str = str(REPO / "config" / "radar" / "stall_1105.json")
    overrides: dict = field(default_factory=dict)  # POSE_KEYS 중 바꾼 값
    restroom: str = ""  # female(여자 화장실) / male(남자 화장실). 비우면 설정 파일 값
    bg_mode: str = "learn"  # learn(시작 후 N초 배우기) / folder(빈 칸 녹화 폴더)
    learn_s: float = 20.0
    bg_folder: str = ""
    room: str = "stall-1"
    device_id: str = "radar-1"
    post: str = ""  # 비우면 서버로 안 보냄
    jsonl: str = ""  # 비우면 저장 안 함
    out_dir: str = str(REPO / "out" / "rt")


def active_stall(s: Settings) -> str:
    """설정 파일에 화면에서 고친 값을 덮어쓴 칸 설정 파일 경로. 고친 값이 없으면 원래 파일."""
    cfg = json.loads(Path(s.stall).read_text(encoding="utf-8"))
    restroom_changed = bool(s.restroom) and s.restroom != cfg.get("restroom", "female")
    if not s.overrides and not restroom_changed:
        return s.stall
    cfg.update({k: v for k, v in s.overrides.items() if k in POSE_KEYS})
    if restroom_changed:
        cfg["restroom"] = s.restroom
    changed = sorted(s.overrides) + (["restroom"] if restroom_changed else [])
    cfg["_active_memo"] = f"{Path(s.stall).name}에 실시간 화면에서 고친 값을 덮어씀: {changed}"
    out = Path(s.out_dir) / "stall_active.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(out)


def _until(frames: Iterable, stop: threading.Event) -> Iterator:
    for f in frames:
        if stop.is_set():
            return
        yield f


def run_session(s: Settings, stop: threading.Event, emit: Callable[[str, object], None]) -> None:
    # 무거운 모듈(pcr_loader 포함)은 여기서 가져온다: 화면이 먼저 뜨게
    from .analysis_common import background_codes, prefix_trim
    from .detector import Detector, learn_background, sim_clock
    from .messages import KST, HttpSink, JsonlSink, envelope
    from .stream import ReplaySource, TcpSource

    try:
        stall_path = active_stall(s)
        if s.source == "replay":
            if not Path(s.replay).is_file():
                raise FileNotFoundError(f"녹화 파일이 없음: {s.replay}")
            skip = prefix_trim(s.replay, s.out_dir)["frames"]
            source = ReplaySource(s.replay, s.speed, skip)
        else:
            host = "127.0.0.1" if s.source == "emulator" else s.host
            source = TcpSource(host, s.port)
            emit("log", f"{host}:{s.port}에 연결하는 중")
        frames = _until(iter(source), stop)

        if s.bg_mode == "folder":
            if not Path(s.bg_folder).is_dir():
                raise FileNotFoundError(f"빈 칸 녹화 폴더가 없음: {s.bg_folder}")
            bg_codes = background_codes(stall_path, s.bg_folder, s.out_dir)
            emit("log", f"배경 칸 {len(bg_codes)}개 (빈 칸 녹화에서)")
            if len(bg_codes) == 0:
                emit("log", "주의: 그 폴더에서 빈 칸 녹화(칸 설정 empty_files)를 못 찾아 배경 없이 탐지함")
        else:
            emit("log", f"배경 배우는 중: {s.learn_s:.0f}초 동안 칸을 비워 두세요")
            emit("learning", s.learn_s)
            bg_codes = learn_background(frames, s.learn_s)
            emit("log", f"배경 칸 {len(bg_codes)}개 배움")
        if stop.is_set():
            return

        http = HttpSink(s.post) if s.post else None
        sinks = [JsonlSink(s.jsonl)] if s.jsonl else []
        if http:
            sinks.append(http)

        def send(m):
            emit("msg", m)
            for k in sinks:
                k.send(m)

        clock = None
        if s.source == "replay":  # 녹화 시각을 '지금'부터 흐르게
            from .radar_io import iter_frames
            t_first = next(iter(iter_frames(s.replay)))[2]
            clock = sim_clock(datetime.now(KST) - timedelta(seconds=t_first))
        det = Detector(stall_path, bg_codes, room=s.room, device_id=s.device_id, clock=clock)
        emit("log", "탐지 시작")
        emit("running", None)

        t_occ = t_stat = t_ui = None
        n_rate, t_rate, fps, total = 0, time.monotonic(), 0.0, 0

        def stats():
            emit("stats", {"connected": bool(source.connected), "fps": round(fps, 1), "frames": total,
                           "sent_ok": http.ok if http else None, "sent_fail": http.errors if http else None,
                           "send_error": http.last_error if http else ""})

        was_connected = True
        for t, pts in frames:
            now = time.monotonic()
            if pts is None:  # 연결 끊김: 열린 사건을 닫고 상태를 바로 알림
                if was_connected:
                    emit("log", f"레이더 연결 끊김: {source.last_error}")
                was_connected = False
                for m in det.close_all():
                    send(m)
                send(envelope("radar.status", s.device_id,
                              {"sensor_ok": False, "fps": 0.0, "error": source.last_error or "레이더 연결 끊김"},
                              datetime.now(KST)))
                t_stat, fps = now, 0.0
                stats()
                continue
            if not was_connected:
                emit("log", "레이더 다시 연결됨")
                was_connected = True
            for m in det.step(t, pts):
                send(m)
            n_rate += 1
            total += 1
            if t_occ is None or t - t_occ >= 1.0:  # radar.occupancy 1초마다
                t_occ = t
                send(det.occupancy())
            if now - t_rate >= 1.0:
                fps, n_rate, t_rate = n_rate / (now - t_rate), 0, now
            if t_stat is None or now - t_stat >= 10.0:  # radar.status 10초마다
                t_stat = now
                error = source.last_error if not source.connected else None
                send(envelope("radar.status", s.device_id,
                              {"sensor_ok": bool(source.connected), "fps": round(fps, 1), "error": error or None},
                              datetime.now(KST)))
            if t_ui is None or now - t_ui >= 1.0:
                t_ui = now
                stats()
        for m in det.close_all():  # 재생이 끝났거나 멈춤
            send(m)
        if http:
            http.flush()
        stats()
        emit("log", "멈춤" if stop.is_set() else "재생 끝")
    except Exception as e:  # noqa: BLE001 - 화면에 그대로 보여 줌
        emit("error", f"{type(e).__name__}: {e}")
    finally:
        emit("done", None)
