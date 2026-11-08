r"""RaFi 레이더 처리 - 레이더 좌표 → 칸 좌표 변환, 칸 범위 자르기, 구역(zone) 판정

칸 좌표 (단위 m)
  X: 왼쪽 벽 → 오른쪽 칸막이,  0 ~ width
  Y: 뒷벽(변기 쪽 벽) → 문,    0 ~ depth
  Z: 바닥 → 위,                0 ~ height
  원점 = 뒷벽과 왼쪽 벽이 만나는 모서리의 바닥
  "왼쪽/오른쪽"은 문 앞에 서서 변기를 바라볼 때 기준

레이더 좌표 (센서가 내보내는 값)
  y: 레이더가 바라보는 방향, x: 레이더 기준 오른쪽, z: 위

설정은 JSON 파일 하나 (예: stall_1102.json)
  radar_pos : 칸 좌표에서 레이더 위치 [X, Y, Z]
  yaw_deg   : 레이더가 바라보는 방향. X축에서 Y축 쪽으로 잰 각도. 문 쪽(+Y)을 보면 90
  pitch_deg : 아래로 숙인 각도. 수평이면 0, 바로 아래를 보면 90
  roll_deg  : 레이더를 정면 축으로 돌린 각도. 거꾸로 매달았으면 180 (생략하면 0)
  mirror_x  : 좌우 뒤집기. 이 칸 좌표(X가 문에서 볼 때 왼쪽→오른쪽)는 왼손 좌표계라서
              레이더가 문 쪽을 보면 true가 맞다. 데이터로 확인하는 방법은 calibrate.py 참고
  zones     : (선택) 접촉 판정 구역 목록 [{name, kind, box: [x0, x1, y0, y1, z0, z1]}]
  fixtures  : (선택) 그림에 그릴 비품 목록 (같은 형식)
  그 밖의 키는 메모로 보고 무시한다.

이전 버전(stall_empty.json, stall_closed.json)도 그대로 읽힌다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

import numpy as np


@dataclass
class Stall:
    width: float
    depth: float
    height: float
    radar_pos: np.ndarray
    yaw_deg: float
    pitch_deg: float = 0.0
    mirror_x: bool = False
    roll_deg: float = 0.0
    zones: list = field(default_factory=list)
    fixtures: list = field(default_factory=list)

    @classmethod
    def load(cls, path) -> Stall:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d["radar_pos"] = np.array(d["radar_pos"], dtype=np.float32)
        return cls(**d)

    def with_pose(self, **kw) -> Stall:
        """자세(pitch, yaw 등)만 바꾼 복사본. calibrate.py에서 쓴다."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d.update(kw)
        if "radar_pos" in kw:
            d["radar_pos"] = np.array(kw["radar_pos"], dtype=np.float32)
        return Stall(**d)

    def axes(self):
        """레이더의 오른쪽(r), 정면(f), 위(u) 방향을 칸 좌표로 나타낸 단위 벡터."""
        psi, p = np.radians(self.yaw_deg), np.radians(self.pitch_deg)
        f = np.array([np.cos(psi) * np.cos(p), np.sin(psi) * np.cos(p), -np.sin(p)])
        u = np.array([np.cos(psi) * np.sin(p), np.sin(psi) * np.sin(p), np.cos(p)])
        r = np.array([np.sin(psi), -np.cos(psi), 0.0])
        if self.roll_deg:
            a = np.radians(self.roll_deg)
            r, u = r * np.cos(a) - u * np.sin(a), u * np.cos(a) + r * np.sin(a)
        if self.mirror_x:
            r = -r
        return r, f, u

    def to_stall(self, pts: np.ndarray) -> np.ndarray:
        """(N, 6) 레이더 좌표 점 배열 → x, y, z만 칸 좌표로 바꾼 새 배열."""
        out = pts.copy()
        if not len(pts):
            return out
        r, f, u = self.axes()
        xyz = pts[:, 0:1] * r + pts[:, 1:2] * f + pts[:, 2:3] * u + self.radar_pos
        out[:, :3] = xyz
        return out

    def inside(self, stall_pts: np.ndarray, margin: float = 0.05) -> np.ndarray:
        """칸 좌표 점들이 칸 안(벽에서 margin 만큼 여유)에 있는지 True/False 배열."""
        X, Y, Z = stall_pts[:, 0], stall_pts[:, 1], stall_pts[:, 2]
        return (
            (X >= -margin) & (X <= self.width + margin)
            & (Y >= -margin) & (Y <= self.depth + margin)
            & (Z >= -margin) & (Z <= self.height + margin)
        )

    def in_box(self, stall_pts: np.ndarray, box) -> np.ndarray:
        """칸 좌표 점들이 상자 [x0, x1, y0, y1, z0, z1] 안에 있는지."""
        x0, x1, y0, y1, z0, z1 = box
        X, Y, Z = stall_pts[:, 0], stall_pts[:, 1], stall_pts[:, 2]
        return (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1) & (Z >= z0) & (Z <= z1)

    def zone_names(self) -> list[str]:
        return [z["name"] for z in self.zones]
