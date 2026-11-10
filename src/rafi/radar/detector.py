"""실시간 설치 의심 행동 판단 (규칙 rule-v3, 버전 기록은 아래).

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

rule-v3 (2026-11-10): 점 사라짐은 다시 나타날 때의 동작으로 판단, 앉은 채 숙임 예외, 벽·문 손 총 시간 (1105 정상 오탐 0)
  - 올라섬 ② 사라짐 (vanish_mode "wait"): 점이 사라지고 문이 안 움직이면 경보하지 않고 다시 나타날 때까지 기다린다.
    다시 나타난 뒤 1초 안 머리가 서 있던 높이 + 0.15 m 이상이고 3초 안에 0.10 m 이상 내려오면 '변기에서 내려옴' → 올라섬 확정.
    앉았다가 일어나서 다시 보이는 것은 서 있던 높이 이하로 나타나므로 경보하지 않는다.
    (위 '실시간에서는 기다릴 수 없어서 바꿈'은 rule-v2까지의 이야기)
  - 앉음 판단: 사라지기 직전 1.5초 안에 머리가 sit_drop(0.30 m) 이상 떨어졌으면 '앉아 있음' 상태로 둔다.
    이 상태는 경보를 끄는 데 쓰지 않고, 머리 숙임 예외에만 쓴다 (아래).
    vanish_mode "timer"(예전 방식)일 때만 사라짐 경보에서 앉음을 뺀다.
  - 머리 숙임: '앉아 있음' 상태에서 일어나기 전(머리가 서 있던 높이 - 0.15 m 아래)에는 세지 않음 (head_rule.skip_seated)
  - 구역 total_window_s: 최근 N초 안 조건이 맞은 시간의 합 ≥ alarm_s면 경보 (디스펜서 없는 벽·문, 30초 중 9초)
  - 구역 door_quiet_s: 문이 움직인 뒤 그 시간 동안은 머묾을 세지 않음
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

METHOD = "rule-v3"  # 서버로 보내는 radar.detection의 method 칸 (규칙 버전)
# 한국인 평균 앉은키(m, 앉은 면 ~ 머리 꼭대기). 제8차 사이즈코리아 비교표의 비장애인 값 (2026-11-10 md 참고)
SITTING_HEIGHT = {"female": 0.859, "male": 0.920}
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
        # 화장실 종류(여자/남자)에 따른 앉은 머리 꼭대기 예상 높이 = 변기 높이 + 한국인 평균 앉은키 (2026-11-10)
        self.restroom = cfg.get("restroom", "female")
        body = cfg.get("body", {})
        seat = body.get("seat_height", [0.30, 0.35])
        sit_h = {**SITTING_HEIGHT, **body.get("sitting_height", {})}[self.restroom]
        self.seated_head = (seat[0] + sit_h, seat[1] + sit_h)
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
        self.zone_tot: dict = {}  # 총 시간 방식 구역: 구역 이름 → deque[(t, dt)] (조건이 맞은 프레임)
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
        self.vanish_kind = None  # 점 사라짐 직전 행동 추정: sit / climb / under / None
        self.seated = False  # 앉아서 사라진 뒤 아직 일어나지 않음 (2026-11-10, 앉은 채 숙임은 머리 숙임에서 뺌)
        self.reapp = None  # 다시 나타난 뒤 머리 추적 {"t0", "vanish_s", "zs": [(t, 머리 높이)]}
        self.hz_hist: deque = deque()  # (t, 머리 높이, 머리 뒷벽 거리) 최근 6초
        self.last_door_t = -1e9
        if hasattr(self, "zone_tot"):
            self.zone_tot.clear()  # 사람이 나가면 쌓인 손 시간도 비움
        if hasattr(self, "head"):
            self.head.reset()

    def _check_reappear(self, t: float, hz: float) -> str | None:
        """다시 나타난 뒤 reappear_win_s(3초) 동안 머리를 본다.
        처음 1초 안 최고 머리 ≥ 서 있던 높이 + down_rise 이고, 그 뒤 최고점보다 down_drop 이상 내려오면
        '변기에서 내려옴'(올라섬 확정) 근거 문장을 돌려준다. 서 있던 높이를 모르면 climb.min_height를 기준으로 쓴다."""
        r = self.reapp
        if r is None:
            return None
        if t - r["t0"] > self.climb.get("reappear_win_s", 3.0):
            self.reapp = None
            return None
        if np.isfinite(hz):
            r["zs"].append((t, hz))
        early = [z for tt, z in r["zs"] if tt - r["t0"] <= 1.0]
        if not early:
            return None
        peak = max(early)
        # 절대 높이는 쓰지 않는다 (키 큰 남성은 서 있어도 1.8 m를 넘을 수 있음). 이 사람의 서 있던 높이 기준 상대값만 본다.
        base = self.base if self.base is not None else self.head.cur_base
        if base is None:
            return None
        if peak < base + self.climb.get("down_rise", 0.15):
            return None
        after = [z for tt, z in r["zs"] if z <= peak - self.climb.get("down_drop", 0.10)]
        if not after:
            return None
        self.reapp = None
        return (f"문이 안 열린 채 {r['vanish_s']:.1f}초 안 보이다가 서 있던 높이 {base:.2f} m보다 {peak - base:.2f} m 높게 "
                f"다시 나타나 {min(after):.2f} m로 내려옴 (변기에서 내려옴 → 올라섬 확정)")

    def _vanish_kind(self, t: float) -> str | None:
        """점이 사라지기 시작한 순간, 직전 머리 움직임으로 행동을 추정한다.
        before = 4 ~ 1.5초 전 머리 높이 중앙값, last = 마지막 1.5초 머리 높이
          before - min(last) ≥ sit_drop → sit (앉음: 경보 안 함)
          max(last) - before ≥ climb_rise → climb (올라섬)
          마지막 머리 뒷벽 거리 ≤ under_max_y → under (레이더 바로 아래로 들어감)"""
        h = [(tt, z, y) for tt, z, y in self.hz_hist if np.isfinite(z)]
        pre = [z for tt, z, _ in h if t - 4.0 <= tt < t - 1.5]
        last = [(z, y) for tt, z, y in h if tt >= t - 1.5]
        if len(pre) < 5 or len(last) < 3:
            return None
        before = float(np.median(pre))
        lz = np.array([z for z, _ in last])
        if before - lz.min() >= self.climb.get("sit_drop", 0.35):
            return "sit"
        if lz.max() - before >= self.climb.get("climb_rise", 0.15):
            return "climb"
        if last[-1][1] <= self.climb.get("under_max_y", 0.70):
            return "under"
        return None

    def _median(self, q: deque, t: float) -> float:
        while q and q[0][0] < t - 1.0:
            q.popleft()
        v = [x for _, x in q if np.isfinite(x)]
        return float(np.median(v)) if v else float("nan")

    # ------------------------------------------------------------ 프레임 하나
    def step(self, t: float, pts: np.ndarray) -> list[dict]:
        """프레임 하나를 넣으면 보낼 radar.detection 메시지 목록을 돌려준다."""
        self.frames += 1
        dt = 0.0 if self.t_last is None else min(max(t - self.t_last, 0.0), 0.5)  # 프레임 간격 (끊김은 0.5초까지만)
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
            if ok and z.get("door_quiet_s") and t - self.last_door_t < z["door_quiet_s"]:
                ok = False  # 문이 막 움직였음 = 드나들며 벽·문을 짚는 중 (2026-11-10, 벽·문 구역)
            win_s = z.get("total_window_s")
            if win_s:
                # (2026-11-10) 총 시간 방식: 최근 total_window_s초 동안 조건이 맞은 시간의 합이 alarm_s 이상이면 경보.
                #   손을 잠깐 뗐다 다시 대도 쌓인다 (실제 설치 = 붙이기, 위치 맞추기, 방향 조정).
                tq = self.zone_tot.setdefault(z["name"], deque())
                if ok:
                    tq.append((t, dt))
                while tq and tq[0][0] < t - float(win_s):
                    tq.popleft()
                total = sum(d for _, d in tq)
                ok_total = total >= float(z["alarm_s"])
                reason = (lambda d, z=z, total=total, win_s=win_s:  # noqa: E731
                          f"{ZONE_KO.get(z['name'], z['name'])} 손 머묾 총 {total:.1f}초 (최근 {float(win_s):.0f}초 중)") if ok_total else None
                out += self._track(z["name"], ok_total, t, 0.0, reason)
                continue
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
        # (2026-11-10) 사라지기 직전 머리가 sit_drop 이상 뚝 떨어졌으면 '앉음'으로 보고 이번 사라짐은 경보에서 뺀다.
        #   1105: 앉을 때 0.43 ~ 0.68 m 떨어짐 / 올라섬·선반 아래는 0.27 m 이하
        # (2026-11-10) vanish_mode "wait": 사라진 동안은 경보하지 않고 다시 나타날 때까지 기다린다.
        #   다시 나타난 머리가 서 있던 높이 + down_rise 이상에서 내려오면 '변기에서 내려옴' → 올라섬 확정.
        #   단, 사라지기 직전 뒷벽 쪽으로 간 경우(under)는 레이더 아래 사각지대 머묾으로 alarm_s초 뒤 경보.
        wait_mode = self.climb.get("vanish_mode", "timer") == "wait"
        vanish_ok = False
        under_ok = False
        if self.seen and sn < 60:
            if self.low_start is None:
                self.low_start = t
                self.vanish_kind = self._vanish_kind(t)
                if self.vanish_kind == "sit":
                    self.seated = True
                # self.reapp은 지우지 않는다: 다시 나타났다 0.5초 만에 또 사라져도(install_vent_hs 14.8 → 15.1초)
                # reappear_win_s 동안은 계속 머리를 본다 (머리 판단은 점이 적어도 15개면 됨)
            if self.last_door_t > self.low_start - 2.0:
                # 문이 움직였다 = 나간 것으로 보고 사람 상태를 비움 (다음 사람은 새로 기준을 잼)
                self.reset_person()
            elif wait_mode:
                # 사각지대 진입은 추정일 뿐(동작은 안 보임) → climb.under_alarm이 true일 때만 경보 (기본 끔)
                under_ok = self.vanish_kind == "under" and bool(self.climb.get("under_alarm", False))
            else:
                vanish_ok = self.vanish_kind != "sit"
        elif sn >= 60:
            if wait_mode and self.low_start is not None and t - self.low_start >= self.climb.get("reappear_min_vanish_s", 1.0):
                self.reapp = {"t0": t, "vanish_s": t - self.low_start, "zs": []}
            self.low_start = None
            self.vanish_kind = None

        # 머리 기준 (rule-v2)
        P = np.asarray(pts, dtype=float)[:, :3] if pts is not None and len(pts) else np.zeros((0, 3))
        if len(P) and len(self.bg):
            P = P[~np.isin(voxel_codes(P, 0.10), self.bg)]
        hr = self.head.update(t, to_stall(P, self.cfg), door_moving=sd >= 3)
        if hr["head"] is not None:
            self.hz_hist.append((t, hr["head"][0], hr["head"][2]))
        while self.hz_hist and self.hz_hist[0][0] < t - 6.0:
            self.hz_hist.popleft()
        hp = self.head.p
        # (2026-11-10) 앉아서 사라졌다가 앉은 채 앞으로 숙여 다시 보이면, 머리가 서 있던 높이보다 낮아도 '서서 숙임'이 아님
        #   (normal_full_hs1: 1.48 → 1.18 m = 여성 앉은 머리 예상 1.16 ~ 1.21 m). 머리가 서 있던 높이 - stand_back까지
        #   돌아오면 일어난 것으로 보고 다시 센다. 1105 위험 녹화의 머리 숙임 경보 11건은 모두 앉음 없이 서서 숙임.
        # 머리 숙임과 같은 기준(head_rule이 잰 서 있던 높이)을 쓴다. 올라섬용 self.base는 낮게 잡힐 때가 있음
        #   (normal_full_kb_1: base 1.34 m, head 기준 1.62 m → 앉은 머리 1.25 m를 '일어남'으로 잘못 봄)
        stand = self.head.cur_base if self.head.cur_base is not None else self.base
        if self.seated and stand is not None and np.isfinite(sh) and sh >= stand - float(hp.get("stand_back", 0.15)):
            self.seated = False
        ok_h = hr["down"] and not (self.seated and hp.get("skip_seated", True))

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

        def climb_reason(d, rise_ok=rise_ok, sh=sh, base=base, kind=self.vanish_kind):
            if rise_ok:
                return f"서 있던 높이({base:.2f} m)보다 {sh - base:.2f} m 높은 상태 {d:.1f}초 (올라섬)"
            # 1105(18°): 레이더는 0.57 m보다 가까운 점을 안 찍고, 정면에서 45° 넘게 아래도 못 본다
            # → 뒷벽에서 약 0.6 m 안쪽은 높이와 상관없이 사각지대 (변기 위에 선 머리, 앉은 사람, 선반 아래)
            guess = {"climb": "직전에 머리가 올라감 → 변기에 올라서서 레이더 앞 사각지대로 들어감 추정",
                     "under": "직전에 뒷벽 쪽으로 감 → 레이더 아래 사각지대(선반 아래·변기 쪽)로 들어감 추정"}.get(kind, "원인 모름")
            return f"문이 안 열렸는데 사람 점이 사라진 상태 {d:.1f}초 ({guess})"

        out += self._track("high", climb_ok, t, float(self.climb["alarm_s"]), climb_reason if climb_ok else None, kind="올라섬")

        if wait_mode:
            # 레이더 아래 사각지대 머묾 (사라지기 직전 뒷벽 쪽으로 감, 서 있는 높이 그대로)
            low_start = self.low_start

            def under_reason(d, low_start=low_start):
                return f"문이 안 열렸는데 뒷벽 쪽으로 가서 안 보이는 상태 {d:.1f}초 (레이더 아래 사각지대: 선반 아래·뒷벽 쪽 추정)"
            out += self._track("under_shelf", under_ok, t, float(self.climb["alarm_s"]), under_reason if under_ok else None,
                               key="under_shelf#vanish", kind="사각지대")

            # 다시 나타남: 높은 머리로 나타나서 내려오면 변기에서 내려온 것
            down = self._check_reappear(t, hr["head"][0] if hr["head"] is not None else np.nan)
            out += self._track("high", down is not None, t, 0.0, (lambda d, s=down: s) if down else None,
                               key="high#down", kind="올라섬")
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
            # 기준에 닿으면 0.5, 두 배면 1.0. 기준 0초(바로 확정하는 판단, 예: 변기에서 내려옴)는 1.0
            ev.score = 1.0 if alarm_s <= 0 else round(min(1.0, d / (2 * alarm_s)), 2)
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
    "wall_plain": "디스펜서 없는 벽",
    "door_hand": "문",
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