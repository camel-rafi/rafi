"""실시간 설치 의심 행동 판단 (규칙 rule-v1).

녹화 분석(analysis_common, evaluate)과 같은 규칙을 '지난 프레임만' 써서 계산한다.
  - 머묾: 지난 2초 창 (녹화 분석은 앞뒤 1초씩). 경보가 약 1초 늦어짐 (kb 확인: 괜찮음)
  - 올라섬 ① 상승: 자리 잡은 뒤 4초 동안 잰 '서 있는 높이'보다 0.2 m 이상 높은 상태
  - 올라섬 ② 사라짐: 사람이 있다가 칸 안 점이 거의 없어졌는데 문은 안 움직임 (문을 안 열었으니 아직 칸 안에 있음)
    (녹화 분석은 '다시 나타남'까지 기다렸지만 실시간에서는 기다릴 수 없어서 바꿈)
  - 기준을 넘는 순간 radar.detection(ongoing=true)을 보내고, 이어지는 동안 update_s마다 갱신, 끝나면 ongoing=false

구역·기준값은 stall json(zones의 alarm_s, tremor, below_head / climb / head_rule)에서 읽는다.

rule-v2 (2026-11-08): 머리 기준 추가 (head_rule.py) + 레이더 의심 하나로 합쳐 보냄
  - 판단은 따로 함: 손 머묾(구역별), 머리 숙임 10초, 선반 아래 선 머리 3초, 올라섬 3초
  - 보내는 것은 하나: 판단 중 하나라도 기준을 넘으면 '레이더 의심' 사건 하나(radar.detection)로 합쳐 보냄
      event_start = 걸린 판단 중 가장 먼저 시작한 시각
      roi        = 손 판단이 걸렸으면 그 구역(점수 높은 것), 아니면 걸린 판단의 구역
                   (올라섬 high, 선반 아래 선 머리 under_shelf, 머리 숙임 stall = 칸 전체.
                    머리 가로 위치로는 어느 비품인지 못 가림: 칸 폭 97 cm, 50°에서 디스펜서 쪽 머리도 X 0.54 ~ 0.65)
      score      = 걸린 판단 점수 중 최댓값 + 종류(손·머리·올라섬)가 둘 이상이면 0.2씩 더함, 1에서 자름
      reasons    = 걸린 판단 전부 (예: ["디스펜서 앞·아래 손 머묾 3.2초", "머리 숙임 11.0초 (...)"])
  - 판단마다 merge_gap_s(2초) 안에 다시 이어지면 같은 사건 → 머리가 기준선 근처에서 잠깐 오르내려도 시간이 이어짐
  - 모든 판단이 merge_gap_s 동안 꺼져 있으면 레이더 의심 사건을 끝냄(ongoing=false)
"""

from __future__ import annotations

import itertools
import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .analysis_common import N_HEAD, N_PER_ZONE, frame_features, voxel_codes
from .head_rule import HeadRule, to_stall
from .messages import KST, envelope, iso
from .stall import Stall

METHOD = "rule-v2"
STAND_MIN = 1.30  # 이보다 낮은 머리 높이는 앉음으로 보고 서 있는 높이 계산에서 뺌 (1102: 앉음 1.04 ~ 1.29, 섬 1.40 ~ 1.60)


class Window:
    """지난 W초 동안의 값 합계 (프레임마다 넣고, 오래된 것은 뺀다)."""

    def __init__(self, seconds: float):
        self.W = seconds
        self.q: deque = deque()
        self.sum = None

    def push(self, t: float, v: np.ndarray):
        v = np.asarray(v, dtype=float)
        self.q.append((t, v))
        self.sum = v.copy() if self.sum is None else self.sum + v
        while self.q and self.q[0][0] < t - self.W:
            _, old = self.q.popleft()
            self.sum -= old

    @property
    def n(self) -> int:
        return len(self.q)

    def values(self, col: int) -> np.ndarray:
        return np.array([v[col] for _, v in self.q])


@dataclass
class Event:
    event_id: str
    roi: str
    start: float  # 탐지기 시각(초)
    last_true: float
    alarmed: bool = False
    last_sent: float = -1e9
    reasons: list = field(default_factory=list)
    score: float = 0.0
    kind: str = "손"  # 손 / 머리 / 올라섬 (합칠 때 종류 수를 셈)


class Detector:
    def __init__(self, stall_path: str | Path, bg_codes: np.ndarray, room: str = "stall-1", device_id: str = "radar-1",
                 window=2.0, presence=0.5, share=0.75, min_pts=2, slow=0.15, moving=0.4,
                 update_s=2.0, merge_gap_s=2.0, clock=None):
        self.st = Stall.load(stall_path)
        cfg = json.loads(Path(stall_path).read_text(encoding="utf-8"))
        self.door_box = (cfg.get("door_plane") or cfg.get("door_swing", {})).get("box")  # 문짝 앞뒤 띠
        self.climb = {"rise": 0.2, "alarm_s": 3.0, **cfg.get("climb", {})}
        self.cfg = cfg
        self.head = HeadRule(cfg)  # 머리 기준 (rule-v2)
        self.zones = [z for z in self.st.zones if "alarm_s" in z]  # 경보에 쓰는 구역만
        self.zidx = {z["name"]: i for i, z in enumerate(self.st.zones)}
        self.bg = bg_codes
        self.room, self.device_id = room, device_id
        self.p = dict(window=window, presence=presence, share=share, min_pts=min_pts, slow=slow, moving=moving)
        self.update_s, self.merge_gap_s = update_s, merge_gap_s
        # 시계: 탐지기 시각(초) → 실제 시각. 기본은 실시간(t = time.monotonic()), 재생은 rt_run·rt_eval이 따로 넘김
        self.clock = clock or live_clock
        self._ids = itertools.count(1)
        self.zw = {z["name"]: Window(window) for z in self.zones}  # 구역별 [점 있음, 전체, 느린, 떨림, 머리차 유효, 머리차 충족]
        self.events: dict[str, Event] = {}  # 판단별 진행 중 상태 (보내지 않음)
        self.ended: dict[str, Event] = {}
        self.combo: Event | None = None  # 합쳐서 보내는 '레이더 의심' 사건
        # 사람 상태 (1초 창): [칸 안 점, 문 움직임, 머리 높이 유효, 몸 중심 유효]
        self.w1 = Window(1.0)
        self.top_q: deque = deque()
        self.by_q: deque = deque()
        self.reset_person()
        self.frames = 0
        self.t_last = None

    # ------------------------------------------------------------ 사람 상태
    def reset_person(self):
        self.seen = False  # 이번 사람이 칸에 나타났는가
        self.base = None  # 서 있는 높이
        self.base_t0 = None
        self.base_vals: list = []
        self.low_start = None
        self.last_door_t = -1e9
        if hasattr(self, "head"):
            self.head.reset()

    def _median(self, q: deque, t: float) -> float:
        while q and q[0][0] < t - 1.0:
            q.popleft()
        v = [x for _, x in q if np.isfinite(x)]
        return float(np.median(v)) if v else float("nan")

    # ------------------------------------------------------------ 프레임 하나
    def step(self, t: float, pts: np.ndarray) -> list[dict]:
        """프레임 하나를 넣으면 보낼 radar.detection 메시지 목록을 돌려준다."""
        self.frames += 1
        self.t_last = t
        row = frame_features(self.st, pts, self.p["moving"], self.p["slow"], self.bg, self.door_box)
        n_in, top, door, body_y = row[1], row[3] / 1000 if row[3] >= 0 else np.nan, row[4], row[5] / 1000 if row[5] >= 0 else np.nan
        out = []

        # 1초 창: 사람 있음, 문 움직임, 머리 높이·몸 중심(중앙값)
        self.w1.push(t, [n_in, door])
        self.top_q.append((t, top))
        self.by_q.append((t, body_y))
        sn, sd = self.w1.sum / max(self.w1.n, 1)
        sh, sy = self._median(self.top_q, t), self._median(self.by_q, t)
        if sd >= 3:
            self.last_door_t = t

        # 구역별 머묾
        for z in self.zones:
            c = N_HEAD + N_PER_ZONE * self.zidx[z["name"]]
            n_all, n_slow, n_trem, zh = row[c], row[c + 1], row[c + 2], row[c + 3]
            gap = top - zh / 1000 if (zh >= 0 and np.isfinite(top)) else np.nan
            bh = z.get("below_head")
            w = self.zw[z["name"]]
            w.push(t, [n_all >= self.p["min_pts"], n_all, n_slow, n_trem, np.isfinite(gap),
                       bool(np.isfinite(gap) and bh is not None and gap >= bh)])
            s = w.sum
            ok = (s[0] / max(w.n, 1) >= self.p["presence"]) and (s[2] / max(s[1], 1) >= self.p["share"])
            if ok and z.get("tremor"):
                ok = s[3] / max(s[1], 1) >= z["tremor"]
            if ok and bh is not None:
                ok = s[4] > 0 and s[5] / s[4] >= 0.5
            reason = None
            if ok:
                extra = []
                if z.get("tremor"):
                    extra.append("손 떨림")
                if bh is not None:
                    extra.append("머리보다 아래")
                reason = lambda d, z=z, extra=extra: f"{ZONE_KO.get(z['name'], z['name'])} 손 머묾 {d:.1f}초" + (f" ({', '.join(extra)})" if extra else "")  # noqa: E731
            out += self._track(z["name"], ok, t, float(z["alarm_s"]), reason)

        # 올라섬 ① 상승
        settled = sn >= 200 and sd < 3 and np.isfinite(sy) and sy <= 0.9
        if sn >= 200:
            self.seen = True
        # 서 있는 높이: 자리 잡은 프레임 중 서 있는 높이대(STAND_MIN ~ 1.9 m)의 값만 모아, 처음 값부터 4초 동안의 중앙값
        # (앉은 머리 1.04 ~ 1.29 m와 문 위쪽 번짐 1.7 ~ 2.0 m가 기준에 섞이지 않게)
        if settled and self.base is None and np.isfinite(sh) and STAND_MIN <= sh < 1.9:
            if self.base_t0 is None:
                self.base_t0 = t
            self.base_vals.append(sh)
            if t - self.base_t0 >= 4.0 and len(self.base_vals) >= 20:
                self.base = float(np.median(self.base_vals))
        calm = sd < 3 and not (np.isfinite(sy) and sy > 0.9)
        rise_ok = (self.base is not None and calm and np.isfinite(sh) and sh >= self.base + self.climb["rise"]
                   and sh >= self.climb.get("min_height", 0.0))

        # 올라섬 ② 사라짐: 사람이 있었고, 점이 거의 없고, 사라지기 2초 전부터 문이 안 움직임
        vanish_ok = False
        if self.seen and sn < 60:
            if self.low_start is None:
                self.low_start = t
            if self.last_door_t > self.low_start - 2.0:
                # 문이 움직였다 = 나간 것으로 보고 사람 상태를 비움 (다음 사람은 새로 기준을 잼)
                self.reset_person()
            else:
                vanish_ok = True
        elif sn >= 60:
            self.low_start = None

        # 머리 기준 (rule-v2)
        P = np.asarray(pts, dtype=float)[:, :3] if pts is not None and len(pts) else np.zeros((0, 3))
        if len(P) and len(self.bg):
            P = P[~np.isin(voxel_codes(P, 0.10), self.bg)]
        hr = self.head.update(t, to_stall(P, self.cfg), door_moving=sd >= 3)
        hp = self.head.p
        ok_h = hr["down"]

        def head_reason(d, hr=hr):
            hz, hx, hy = hr["head"]
            return (f"머리 숙임 {d:.1f}초 (서 있던 높이 {self.head.cur_base:.2f} m → {hz:.2f} m, "
                    f"머리 위치 왼쪽 벽에서 {hx:.2f} m · 뒷벽에서 {hy:.2f} m)")
        out += self._track("stall", ok_h, t, float(hp["alarm_s"]), head_reason if ok_h else None, key="head_down#head", kind="머리")

        def shelf_reason(d, hr=hr):
            return f"선반 아래 선 머리 {d:.1f}초 (머리 {hr['head'][0]:.2f} m, 뒷벽에서 {hr['head'][2]:.2f} m)"
        out += self._track("under_shelf", hr["shelf"], t, float(hp["shelf_alarm_s"]), shelf_reason if hr["shelf"] else None,
                           key="under_shelf#head", kind="머리")

        climb_ok = rise_ok or vanish_ok
        base = self.base

        def climb_reason(d, rise_ok=rise_ok, sh=sh, base=base):
            if rise_ok:
                return f"서 있던 높이({base:.2f} m)보다 {sh - base:.2f} m 높은 상태 {d:.1f}초 (올라섬)"
            return f"문이 안 열렸는데 사람 점이 사라진 상태 {d:.1f}초 (올라서서 시야 위로 나감)"

        out += self._track("high", climb_ok, t, float(self.climb["alarm_s"]), climb_reason if climb_ok else None, kind="올라섬")
        out += self._combine(t)
        return out

    # ------------------------------------------------------------ 사건 관리
    def _track(self, roi: str, ok: bool, t: float, alarm_s: float, reason, key: str | None = None,
               kind: str = "손") -> list[dict]:
        """판단 하나의 상태만 관리한다 (메시지는 _combine이 합쳐서 보냄).
        조건이 merge_gap_s 안에 다시 맞으면 같은 사건으로 이어 붙인다.
        key: 같은 roi에 판단이 여러 개일 때(손 / 머리) 따로 관리하는 이름. 없으면 roi."""
        roi_key = key or roi
        ev = self.events.get(roi_key)
        if ok:
            if ev is None:
                prev = self.ended.pop(roi_key, None)
                if prev is not None and t - prev.last_true <= self.merge_gap_s:
                    ev = prev  # 잠깐 끊겼다 이어짐 = 같은 판단 사건
                else:
                    ev = Event(event_id=f"{roi_key}", roi=roi, start=t, last_true=t, kind=kind)
                self.events[roi_key] = ev
            ev.last_true = t
            d = t - ev.start
            ev.reasons = [reason(d)]
            ev.score = round(min(1.0, d / (2 * alarm_s)), 2)  # 기준에 닿으면 0.5, 두 배면 1.0
            if d >= alarm_s:
                ev.alarmed = True
        else:
            if ev is not None:
                del self.events[roi_key]
                self.ended[roi_key] = ev  # 바로 버리지 않고 merge_gap_s 동안 기다림
            prev = self.ended.get(roi_key)
            if prev is not None and t - prev.last_true > self.merge_gap_s:
                del self.ended[roi_key]
        return []

    def _combine(self, t: float) -> list[dict]:
        """기준을 넘은 판단들을 '레이더 의심' 사건 하나로 합쳐 보낸다."""
        out = []
        active = [ev for ev in list(self.events.values()) + list(self.ended.values())
                  if ev.alarmed and t - ev.last_true <= self.merge_gap_s]
        c = self.combo
        if active:
            active.sort(key=lambda e: -e.score)
            if c is None:
                c = Event(event_id=f"{self.device_id}-{next(self._ids)}", roi="", start=min(e.start for e in active),
                          last_true=t)
                self.combo = c
            c.start = min(c.start, min(e.start for e in active))
            c.last_true = t
            hand = [e for e in active if e.kind == "손"]
            c.roi = hand[0].roi if hand else active[0].roi
            kinds = {e.kind for e in active}
            c.score = round(min(1.0, max(e.score for e in active) + 0.2 * (len(kinds) - 1)), 2)
            c.reasons = [e.reasons[0] for e in active]
            if not c.alarmed or t - c.last_sent >= self.update_s:
                c.alarmed = True
                c.last_sent = t
                out.append(self._msg(c, ongoing=True))
        elif c is not None and t - c.last_true > self.merge_gap_s:
            out.append(self._msg(c, ongoing=False))
            self.combo = None
        return out

    def close_all(self) -> list[dict]:
        """연결이 끊기거나 재생이 끝났을 때: 열린 레이더 의심 사건을 끝남으로 보냄."""
        out = [self._msg(self.combo, ongoing=False)] if self.combo is not None and self.combo.alarmed else []
        self.combo = None
        self.events.clear()
        self.ended.clear()
        return out

    def _msg(self, ev: Event, ongoing: bool) -> dict:
        payload = {
            "source": "radar",
            "event_start": iso(self.clock(ev.start)),
            "event_end": iso(self.clock(ev.last_true)),
            "room": self.room,
            "roi": ev.roi,
            "device": None,
            "rssi_mean": None,
            "score": ev.score,
            "method": METHOD,
            "reasons": ev.reasons,
            # 아래는 내부용(화면·평가). 서버 규격에 없어서 to_server()가 빼고 보냄
            # (room은 서버가 device_id로 설정 파일에서 찾음)
            "event_id": ev.event_id,
            "ongoing": ongoing,
        }
        return envelope("radar.detection", self.device_id, payload, self.clock(self.t_last))

    # ------------------------------------------------------------ 주기 메시지
    def occupancy(self) -> dict:
        sn = self.w1.sum[0] / max(self.w1.n, 1) if self.w1.sum is not None else 0
        hidden = self.low_start is not None and self.seen
        here = bool(sn >= 60 or hidden)
        zones = [name for name, w in self.zw.items() if w.n and w.sum[0] / w.n >= 0.5]
        payload = {"person_count": int(here), "zones": zones,
                   # 아래는 내부용(화면·평가). 서버로는 to_server()가 빼고 보냄
                   "hidden": bool(hidden),  # 사람이 있다고 보지만 점이 안 보이는 상태 (올라섬 등)
                   "stand_height": round(self.base, 2) if self.base is not None else None}
        return envelope("radar.occupancy", self.device_id, payload, self.clock(self.t_last or 0))


ZONE_KO = {
    "dispenser_low": "디스펜서 앞·아래",
    "dispenser_top": "디스펜서 위쪽",
    "sanbox_low": "위생함 앞·아래",
    "sanbox_top": "위생함 위쪽",
    "under_shelf": "선반 아래",
    "stall": "칸 전체",
    "high": "높은 곳",
}


def learn_background(frames, seconds: float, vox=0.10, min_rate=0.10) -> np.ndarray:
    """빈 칸에서 처음 seconds초 동안 받은 프레임으로 배경(늘 찍히는 10 cm 칸)을 만든다."""
    counts, n, t0 = {}, 0, None
    for t, pts in frames:
        if pts is None:
            continue
        t0 = t if t0 is None else t0
        if t - t0 > seconds:
            break
        n += 1
        if len(pts):
            for c in np.unique(voxel_codes(pts[:, :3], vox)).tolist():
                counts[c] = counts.get(c, 0) + 1
    return np.array(sorted(c for c, k in counts.items() if k / max(n, 1) >= min_rate), dtype=np.int64)


def live_clock(t: float) -> datetime:
    """실시간: time.monotonic() 시각 t → 실제 시각 (지금 시각에서 경과만큼 뺌)."""
    return datetime.now(KST) - timedelta(seconds=time.monotonic() - t)


def sim_clock(start: datetime):
    """재생 평가용: 녹화 시각(초) → start + 그 초."""
    return lambda t: start + timedelta(seconds=float(t))