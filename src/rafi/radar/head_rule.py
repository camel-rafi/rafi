"""머리 기준 판단 (rule-v2에서 추가, 2026-11-08).

11-02(50°)·11-05(18°) 녹화 분석 결과(docs/report_1105_notion.md 8장)를 실시간으로 옮긴 것.

  1. 머리 숙임 (디스펜서·위생함 아래 설치)
     머리가 '서 있던 높이 - drop' 이하, min_height 이상, 뒷벽에서 min_y 이상(변기 위가 아님)
     → 이 상태가 alarm_s초(10초) 이어지면 경보 (roi 'head_down')
  2. 선반 아래 (선 머리)
     머리가 shelf_min_height 이상(서 있음)이고 뒷벽에서 shelf_max_y 안 → shelf_alarm_s초(3초)

머리 = 칸 안 사람 점(배경 뺌) 중 가장 높은 점 5개의 중앙값. 머리 가로 위치 = 그 높이에서 25 cm 아래까지 점의 중앙값.
smooth_s(0.5초) 동안의 중앙값으로 흔들림을 줄인다.
analysis_common의 '머리 높이'(뒷벽에서 0.85 m까지만 봄)와 달리 칸 전체에서 찾는다.
쭈그린 머리가 뒷벽에서 0.97 ~ 1.05 m에 찍히기 때문(11-05).
"""
from __future__ import annotations

from collections import deque

import numpy as np

DEFAULT = {
    "drop": 0.25,          # 서 있던 높이보다 이만큼 이상 내려가면 숙임
    "min_height": 0.95,    # 이보다 낮으면 줍기·휴대폰처럼 깊게 숙인 것으로 보고 뺌
    "min_ratio": None,     # (2026-11-12) 머리가 '서 있던 높이 × 이 비율'보다 낮으면 바닥 찾기·줍기로 보고 뺌. None = 안 씀
    "min_y": 0.60,         # 뒷벽에서 이 거리 이상 = 변기 위(앉은 머리 0.42 ~ 0.49)가 아님
    "alarm_s": 10.0,
    "stand_min": 1.30,     # 서 있는 높이로 셀 머리 범위
    "stand_max": 1.90,
    "stand_s": 4.0,        # 서 있는 높이를 재는 시간
    "stand_s_min": 1.0,    # 4초가 차기 전에도 이만큼 모이면 임시 기준으로 씀 (들어오자마자 숙이는 경우)
    "smooth_s": 0.5,
    "min_pts": 15,         # 칸 안 사람 점이 이보다 적으면 '안 보임'
    "shelf_min_height": 1.30,
    "shelf_max_y": 0.40,
    "shelf_alarm_s": 3.0,
    "shelf_max_drop": None,  # (2026-11-12) 서 있던 높이보다 이만큼 넘게 낮으면 선반 판단에서 뺌(깊게 숙임·앉음). None = 안 씀
    "shelf_skip_seated": False,  # (2026-11-12) Detector가 '앉음'으로 본 동안은 선반 판단에서 뺌
    "shelf_max_rise": 0.10,  # 서 있던 높이보다 이만큼 넘게 높으면 올라선 것(환풍기)으로 보고 선반 판단에서 뺌
}


def to_stall(pts: np.ndarray, cfg: dict) -> np.ndarray:
    """레이더 좌표(x 옆, y 앞, z 위) → 칸 좌표(X 왼쪽 벽에서, Y 뒷벽에서, Z 바닥에서).
    yaw 90 · roll 0 · 레이더가 뒷벽 쪽에서 문 쪽을 보는 설치만 지원 (11-02 · 11-05 칸)."""
    if abs(cfg.get("yaw_deg", 90) - 90) > 1e-6 or abs(cfg.get("roll_deg", 0)) > 1e-6:
        raise ValueError("head_rule.to_stall은 yaw 90, roll 0만 지원")
    th = np.deg2rad(cfg["pitch_deg"])
    px, py, pz = cfg["radar_pos"]
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    X = px - x if cfg.get("mirror_x", False) else px + x
    Y = py + y * np.cos(th) + z * np.sin(th)
    Z = pz - y * np.sin(th) + z * np.cos(th)
    return np.stack([X, Y, Z], 1)


class HeadRule:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.p = {**DEFAULT, **cfg.get("head_rule", {})}
        W, D = cfg["width"], cfg["depth"]
        self.box = (-0.05, W + 0.05, -0.05, min(D - 0.075, 1.30), 0.05, cfg["height"] - 0.03)
        self.q: deque = deque()  # (t, 머리 높이, 머리 X, 머리 Y)
        self.reset()

    def reset(self):
        """사람이 나갔을 때 (Detector.reset_person에서 부름)."""
        self.base = None
        self.cur_base = None
        self._base_t0 = None
        self._base_vals: list = []

    # ------------------------------------------------------------------
    def head(self, P: np.ndarray):
        """칸 좌표 점(배경 뺀 것) → (머리 높이, 머리 X, 머리 Y) 또는 None."""
        x0, x1, y0, y1, z0, z1 = self.box
        m = (P[:, 0] > x0) & (P[:, 0] < x1) & (P[:, 1] > y0) & (P[:, 1] < y1) & (P[:, 2] > z0) & (P[:, 2] < z1)
        m &= ~((P[:, 1] > 1.0) & (P[:, 2] > 1.8))  # 문 위쪽 번짐
        q = P[m]
        if len(q) < self.p["min_pts"]:
            return None
        top = float(np.median(np.sort(q[:, 2])[-5:]))
        h = q[q[:, 2] > top - 0.25]
        return top, float(np.median(h[:, 0])), float(np.median(h[:, 1]))

    def update(self, t: float, P: np.ndarray, door_moving: bool):
        """프레임 하나. 반환: dict(head=(높이, X, Y) 또는 None, down=머리 숙임 여부, shelf=선반 아래 선 머리 여부)."""
        r = self.head(P)
        self.q.append((t, *(r if r else (np.nan,) * 3)))
        while self.q and self.q[0][0] < t - self.p["smooth_s"]:
            self.q.popleft()
        a = np.array([v[1:] for v in self.q], dtype=float)
        ok = np.isfinite(a[:, 0])
        if not ok.any():
            return {"head": None, "down": False, "shelf": False}
        hz, hx, hy = np.median(a[ok], axis=0)

        # 서 있는 높이: 문이 조용하고 문에서 떨어진 상태에서 stand_min ~ stand_max인 머리를 stand_s초 모아 중앙값
        if self.base is None and not door_moving and hy <= 1.1 and self.p["stand_min"] <= hz < self.p["stand_max"]:
            if self._base_t0 is None:
                self._base_t0 = t
            self._base_vals.append(hz)
            if t - self._base_t0 >= self.p["stand_s"] and len(self._base_vals) >= 20:
                self.base = float(np.median(self._base_vals))
        base = self.base
        if base is None and self._base_t0 is not None and t - self._base_t0 >= self.p["stand_s_min"]:
            base = float(np.median(self._base_vals))  # 임시 기준
        self.cur_base = base

        down = (base is not None and hz <= base - self.p["drop"]
                and hz >= self.p["min_height"] and hy >= self.p["min_y"]
                and (self.p.get("min_ratio") is None or hz >= base * self.p["min_ratio"]))
        shelf = ((not door_moving) and hz >= self.p["shelf_min_height"] and hy <= self.p["shelf_max_y"]
                 and (base is None or hz <= base + self.p["shelf_max_rise"])
                 and (self.p.get("shelf_max_drop") is None or base is None or hz >= base - self.p["shelf_max_drop"]))
        return {"head": (hz, hx, hy), "down": bool(down), "shelf": bool(shelf)}