r"""RaFi 레이더 처리 - 좌우(mirror_x) 확인: 왼쪽 벽 동작과 오른쪽 벽 동작이 제자리에 찍히는가

움직이는 점(손·팔)만 쓴다. 늘 같은 자리에 찍히는 배경·허상은 움직이지 않으므로 빠진다.
① 좌우 설정 없이 레이더가 준 x 숫자 그대로 비교: 휴지 동작의 손이 x가 큰 쪽이면 '레이더 x가 커지는 쪽 = 왼쪽 벽'(true)
② 칸 좌표로 바꿔서도 비교 (참고)

구역 상자와 상관없이, 칸 안 점들의 좌우 위치(X, 0 = 왼쪽 벽 디스펜서 쪽, 0.97 = 오른쪽 칸막이 위생함 쪽)만 본다.
  - 휴지 동작 (danger_tissue, danger_tissue_sitting, tissue_use): 사람이 왼쪽 벽 디스펜서 쪽으로 감 → X가 작아야 함
  - 위생함 동작 (pad_bin): 오른쪽 칸막이 위생함 쪽으로 감 → X가 커야 함
  - 앉아 있기 (sitting_still): 가운데 변기 (참고)
mirror_x를 true / false 둘 다로 계산해서, 휴지 쪽이 위생함 쪽보다 왼쪽(X 작음)에 오는 설정이 맞는 설정이다.
레이더(X 0.63)에서 디스펜서는 옆으로 56 cm 떨어져 있어서, 좌우가 틀리면 1 m 넘게 반대쪽으로 넘어간다. 변기(15 cm)보다 훨씬 확실한 시험.

사용 예:
    python experiments/radar/side_check.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
"""

from __future__ import annotations

import argparse

import numpy as np

from rafi.radar.analysis_common import list_pcr, parse_name
from rafi.radar.radar_io import iter_frames
from rafi.radar.stall import Stall

GROUPS = {
    "휴지 동작 (왼쪽 벽)": ("danger_tissue", "danger_tissue_sitting", "tissue_use"),
    "위생함 동작 (오른쪽 칸막이)": ("pad_bin",),
    "앉아 있기 (가운데 변기)": ("sitting_still",),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--every", type=int, default=2, help="프레임 몇 개마다 하나씩")
    ap.add_argument("--moving", type=float, default=0.3, help="이 |doppler|(m/s)보다 빠른 점만 = 움직이는 손·팔. 0이면 전부")
    ap.add_argument("--max-range", type=float, default=2.4, help="레이더에서 이 거리(m) 안의 점만 (칸 밖 먼 허상 제거)")
    a = ap.parse_args()

    base = Stall.load(a.stall)
    raw: dict[str, list] = {g: [] for g in GROUPS}
    per_file = []
    for p in list_pcr(a.folder):
        act = parse_name(p)["action"]
        g = next((k for k, acts in GROUPS.items() if act in acts), None)
        if g is None:
            continue
        pts = []
        for i, (_, _, _, q) in enumerate(iter_frames(str(p))):
            if i % a.every or not len(q):
                continue
            q = q[(np.abs(q[:, 3]) > a.moving) & (np.linalg.norm(q[:, :3], axis=1) <= a.max_range)]
            if len(q):
                pts.append(q)
        if pts:
            pts = np.concatenate(pts)
            raw[g].append(pts)
            per_file.append((g, p.name, pts))

    print(f"움직이는 점만 사용: |doppler| > {a.moving} m/s, 레이더 {a.max_range} m 안\n")
    print("① 좌우 설정 없이: 레이더가 준 x 숫자 그대로 (중앙값, m)")
    xr = {}
    for g, chunks in raw.items():
        if chunks:
            q = np.concatenate(chunks)
            xr[g] = float(np.median(q[:, 0]))
            print(f"   {g:22s} 레이더 x 중앙값 {xr[g]:+.2f}  (점 {len(q):,}개)")
    tg0, pg0 = list(GROUPS)[0], list(GROUPS)[1]
    if tg0 in xr and pg0 in xr:
        side = "큰" if xr[tg0] > xr[pg0] else "작은"
        print(f"   → 휴지(왼쪽 벽) 동작이 레이더 x가 {side} 쪽에 찍힘 → 레이더 x가 커지는 쪽 = "
              f"{'휴지 쪽(왼쪽 벽) → mirror_x true' if side == '큰' else '위생함 쪽(오른쪽 칸막이) → mirror_x false'}"
              f"  (차이 {abs(xr[tg0] - xr[pg0]):.2f} m)")
    print("\n② 칸 좌표로 바꿔서 (참고)")
    print(f"레이더 ({base.radar_pos[0]:.2f}, {base.radar_pos[1]:.2f}, {base.radar_pos[2]:.2f}), pitch {base.pitch_deg:.0f}°, yaw {base.yaw_deg:.0f}°")
    print("칸 안 점의 좌우 위치 X 중앙값 (0 = 왼쪽 벽 디스펜서 쪽, 0.97 = 오른쪽 칸막이 위생함 쪽)\n")
    res = {}
    for mirror in (False, True):
        st = base.with_pose(mirror_x=mirror)
        print(f"mirror_x = {str(mirror).lower()}")
        res[mirror] = {}
        for g, chunks in raw.items():
            if not chunks:
                continue
            sp = st.to_stall(np.concatenate(chunks))
            m = st.inside(sp, 0.0)
            med = float(np.median(sp[m, 0])) if m.any() else float("nan")
            res[mirror][g] = med
            print(f"   {g:22s} X 중앙값 {med:.2f}  (칸 안 점 {m.sum():,}개, 칸 밖으로 나간 점 {(~m).mean():.0%})")
        print()

    tg, pg = list(GROUPS)[0], list(GROUPS)[1]
    print("판단: 휴지 쪽이 위생함 쪽보다 왼쪽(X 작음)이어야 맞음")
    for mirror in (False, True):
        t, pd = res[mirror].get(tg, np.nan), res[mirror].get(pg, np.nan)
        ok = t < pd
        print(f"   mirror_x = {str(mirror).lower():5s}: 휴지 {t:.2f} vs 위생함 {pd:.2f} → {'맞음' if ok else '반대'} (차이 {pd - t:+.2f} m)")

    print("\n파일별 (mirror_x 두 경우)")
    for _g, name, pts in per_file:
        xs = []
        for mirror in (False, True):
            st = base.with_pose(mirror_x=mirror)
            sp = st.to_stall(pts)
            m = st.inside(sp, 0.0)
            xs.append(float(np.median(sp[m, 0])) if m.any() else float("nan"))
        print(f"   {name:32s} 레이더 x {np.median(pts[:, 0]):+.2f} | 칸 X: false {xs[0]:.2f} / true {xs[1]:.2f}  (점 {len(pts):,}개)")


if __name__ == "__main__":
    main()