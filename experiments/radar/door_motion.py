r"""RaFi 레이더 처리 - 문 움직임 분석 (shaking_door, entering, exiting)

shaking_door_1, 2는 칸 밖에서 문만 건드려 흔든 녹화다 (흔들림이 점점 느려짐).
들어올 때·나갈 때 생기는 '문 움직임'을 사람 움직임과 구분하려고 찍었다.

프레임마다 계산
  - 문 범위(stall_1102.json의 door_swing: 문이 안팎으로 돌아가는 범위) 안의 점
      전체 개수, 움직이는 점 개수(|doppler| > --moving), 가장 빠른 |doppler|
      칸 안쪽(Y < 깊이)인지 바깥쪽인지 나눠서 셈 → 문이 어느 쪽으로 열리는지
  - 문 범위 밖, 칸 안의 움직이는 점 개수 → '사람 의심 점'
      shaking_door에서는 칸 안에 사람이 없으므로 이 값이 0 근처여야 한다.
      0이 아니면 문 흔들림이 칸 안 사람 움직임으로 잘못 보인다는 뜻 (반사 허상 등)

출력 (out\1102\door)
  door_summary.csv : 파일별 요약 (움직인 시간, 최고 속도, 앞/뒤 1/3 속도, 안쪽 비율, 사람 의심 프레임)
  <파일>.png       : 시간축 그림 + 위에서 본 문 점 (색 = 시각)

사용 예:
    python experiments/radar/door_motion.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
    python experiments/radar/door_motion.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json --only "entering_*"
    python experiments/radar/door_motion.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json --only "exiting_*"
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from rafi.radar.analysis_common import list_pcr, setup_fonts
from rafi.radar.radar_io import iter_frames
from rafi.radar.stall import Stall


def analyze(pcr, st: Stall, box, moving):
    rows, door_pts = [], []
    for _, _, t, pts in iter_frames(str(pcr)):
        if not len(pts):
            rows.append((t, 0, 0, 0, 0, 0.0, 0))
            continue
        sp = st.to_stall(pts)
        d = np.abs(sp[:, 3])
        in_door = st.in_box(sp, box)
        mv = d > moving
        inner = sp[:, 1] < st.depth
        stall_in = st.inside(sp, 0.0)
        other = stall_in & ~in_door & mv  # 문 범위 밖, 칸 안, 움직이는 점
        dm = in_door & mv
        rows.append((t, int(in_door.sum()), int(dm.sum()), int((dm & inner).sum()), int((dm & ~inner).sum()),
                     float(d[dm].max()) if dm.any() else 0.0, int(other.sum())))
        if dm.any():
            q = sp[dm][:, :4].copy()
            door_pts.append(np.column_stack([q, np.full(len(q), t)]))
    arr = np.array(rows, dtype=float)
    dp = np.concatenate(door_pts) if door_pts else np.zeros((0, 5))
    return arr, dp


def summarize(name, arr, min_door_pts, person_pts):
    t, n_door, n_mv, n_in, n_out, vmax, n_other = arr.T
    active = n_mv >= min_door_pts
    if active.any():
        ta, tb = t[active][0], t[active][-1]
        seg = (t >= ta) & (t <= tb)
        v = vmax[seg & active]
        k = max(1, len(v) // 3)
        first, last = float(np.mean(v[:k])), float(np.mean(v[-k:]))
    else:
        ta = tb = first = last = float("nan")
    tot_mv = n_in.sum() + n_out.sum()
    return {
        "file": name,
        "frames": len(t),
        "door_move_start_s": round(ta, 2),
        "door_move_end_s": round(tb, 2),
        "door_move_frames": int(active.sum()),
        "vmax_peak": round(float(vmax.max()), 3),
        "vmax_first_third": round(first, 3),
        "vmax_last_third": round(last, 3),
        "inner_share": round(float(n_in.sum() / tot_mv), 2) if tot_mv else "",
        "person_like_frames": int((n_other >= person_pts).sum()),
        "person_like_frames_during_door": int(((n_other >= person_pts) & active).sum()),
    }


def draw(name, arr, dp, st: Stall, box, a, path):
    plt = setup_fonts()
    import matplotlib.patches as mp

    t, n_door, n_mv, n_in, n_out, vmax, n_other = arr.T
    fig = plt.figure(figsize=(15, 7))
    g = fig.add_gridspec(3, 2, width_ratios=[2.6, 1])
    ax1 = fig.add_subplot(g[0, 0])
    ax1.plot(t, n_in, lw=0.8, label="문 범위 움직이는 점 (칸 안쪽)")
    ax1.plot(t, n_out, lw=0.8, label="문 범위 움직이는 점 (칸 바깥쪽)")
    ax1.axhline(a.min_door_pts, ls="--", lw=0.6, color="k")
    ax1.legend(fontsize=7, loc="upper right")
    ax1.set_title(name, loc="left", fontsize=10)
    ax2 = fig.add_subplot(g[1, 0], sharex=ax1)
    ax2.plot(t, vmax, lw=0.8, color="tab:red")
    ax2.set_ylabel("문 점 최고 |doppler| (m/s)", fontsize=8)
    ax3 = fig.add_subplot(g[2, 0], sharex=ax1)
    ax3.plot(t, n_other, lw=0.8, color="tab:purple")
    ax3.axhline(a.person_pts, ls="--", lw=0.6, color="k")
    ax3.set_ylabel("칸 안·문 범위 밖\n움직이는 점", fontsize=8)
    ax3.set_xlabel("시각 (초)")
    for axis in (ax1, ax2, ax3):
        axis.grid(alpha=0.3)
        axis.tick_params(labelsize=8)

    ax = fig.add_subplot(g[:, 1])
    if len(dp):
        sc = ax.scatter(dp[:, 0], dp[:, 1], c=dp[:, 4], s=4, cmap="viridis", alpha=0.6)
        fig.colorbar(sc, ax=ax, label="시각 (초)", shrink=0.7)
    ax.add_patch(mp.Rectangle((0, 0), st.width, st.depth, fill=False, lw=1.2, color="k"))
    ax.add_patch(mp.Rectangle((box[0], box[2]), box[1] - box[0], box[3] - box[2], fill=False, lw=1, ls="--", color="tab:blue"))
    ax.plot([0.38, 0.97], [st.depth, st.depth], lw=3, color="tab:orange")
    ax.plot(st.radar_pos[0], st.radar_pos[1], "r^", ms=9)
    ax.set_xlim(-0.2, st.width + 0.3)
    ax.set_ylim(-0.2, box[3] + 0.2)
    ax.set_aspect("equal")
    ax.set_xlabel("X 왼쪽→오른쪽", fontsize=8)
    ax.set_ylabel("Y 뒷벽→문 (1.375 = 문)", fontsize=8)
    ax.set_title("위에서 본 문 범위의 움직이는 점", fontsize=9)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--only", default="shaking_door_*.pcr")
    ap.add_argument("--moving", type=float, default=0.15, help="문 움직임으로 볼 |doppler| (m/s). 문은 느리므로 사람(0.4)보다 낮게")
    ap.add_argument("--min-door-pts", type=int, default=3, help="문 범위 움직이는 점이 이 개수 이상이면 '문이 움직임'")
    ap.add_argument("--person-pts", type=int, default=5, help="칸 안·문 범위 밖 움직이는 점이 이 개수 이상이면 '사람 의심'")
    ap.add_argument("--out", default=r"out\1102\door")
    a = ap.parse_args()

    st = Stall.load(a.stall)
    box = json.loads(Path(a.stall).read_text(encoding="utf-8"))["door_swing"]["box"]
    out = Path(a.out)
    rows = []
    for p in list_pcr(a.folder, a.only):
        arr, dp = analyze(p, st, box, a.moving)
        r = summarize(p.name, arr, a.min_door_pts, a.person_pts)
        rows.append(r)
        draw(p.name, arr, dp, st, box, a, out / f"{p.stem}.png")
        print(f"{p.name:32s} 문 움직임 {r['door_move_start_s']}~{r['door_move_end_s']}s, 최고 {r['vmax_peak']} m/s,"
              f" 앞 1/3 {r['vmax_first_third']} → 뒤 1/3 {r['vmax_last_third']}, 안쪽 비율 {r['inner_share']},"
              f" 사람 의심 프레임 {r['person_like_frames']} (문 움직일 때 {r['person_like_frames_during_door']})")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "door_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"\n저장: {out / 'door_summary.csv'}, 그림 {out}")
    print("읽는 법: 안쪽 비율이 1에 가까우면 문이 칸 안쪽으로 열림. shaking_door에서 사람 의심 프레임이 많으면"
          " 문 흔들림이 칸 안 움직임으로 새어 들어온다는 뜻 → 판단에서 문 범위를 따로 빼야 함")


if __name__ == "__main__":
    main()
