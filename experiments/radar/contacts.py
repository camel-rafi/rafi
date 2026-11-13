r"""RaFi 레이더 처리 - 비품·벽에 '붙어 있는' 구간 후보 자동 찾기 + 확인용 그림

녹화 때 손이 비품에 닿은 시각을 따로 적지 못했으므로, 데이터에서 후보를 자동으로 뽑고 사람이 그림을 보고 고친다.

후보 기준 (기본값, 옵션으로 바꿀 수 있음)
  - 칸 좌표로 바꾼 뒤, 구역(디스펜서, 위생용품 함, 선반 아래, 벽 4면) 안의
  - 정지 점(|doppler| ≤ 0.08 m/s, 도플러 0~1칸)이
  - 0.5초 이동 평균으로 3개 이상인 상태가
  - 1초 이상 이어지면 (0.5초 이하로 끊긴 곳은 이어 붙임) 후보 구간 하나

출력 (out\1102 아래)
  features\*.npz           프레임별 특징 (설정이 같으면 다시 계산하지 않음)
  labels_auto.csv          후보 구간 전부. 복사해서 labels.csv로 만든 뒤 고친다
  review\<파일>.png        파일마다 확인용 그림
                           왼쪽: 시간축 (칸 안 점 / 움직이는 점 / 구역별 정지 점, 후보 구간은 색칠)
                           오른쪽: 그 파일 목표 구역의 가장 긴 후보 구간 동안 점을 위·옆에서 본 그림

사용 예:
    python experiments/radar/contacts.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
    python experiments/radar/contacts.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json --only "danger_tissue_*"
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from rafi.radar.analysis_common import (
    add_segment_args,
    apply_zones,
    compute_features,
    list_pcr,
    parse_name,
    seg_kw,
    segments_by_session,
    setup_fonts,
)
from rafi.radar.radar_io import iter_frames
from rafi.radar.stall import Stall

ZONE_COLOR = {"dispenser_low": "tab:green", "dispenser_top": "yellowgreen", "sanbox_low": "tab:orange",
              "sanbox_top": "gold", "under_shelf": "tab:purple",
              "wall_left": "tab:olive", "wall_right": "tab:brown", "wall_back": "tab:gray", "door": "tab:cyan"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--out", default=r"out\1102")
    ap.add_argument("--only", default="*.pcr", help="파일 이름 패턴 (예: danger_*)")
    ap.add_argument("--no-plot", action="store_true")
    ap.add_argument("--focus", default=None, help="확인 그림 오른쪽에 이 구역의 가장 긴 구간을 그림 (예: wall_right)")
    ap.add_argument("--no-cache", action="store_true")
    add_segment_args(ap)
    a = ap.parse_args()

    st = apply_zones(Stall.load(a.stall), a.zones)
    out = Path(a.out)
    rows = []
    for p in list_pcr(a.folder, a.only):
        info = parse_name(p)
        feat = compute_features(p, a.stall, out, a.moving, a.static, use_cache=not a.no_cache, zones_path=a.zones)
        segs = {}
        for z in st.zone_names():
            segs[z] = segments_by_session(feat, z, **seg_kw(a))
            for s, t0, t1, mean in segs[z]:
                rows.append({"file": info["file"], "session": s, "zone": z, "start_s": round(t0, 2), "end_s": round(t1, 2),
                             "dur_s": round(t1 - t0, 2), "mean_pts": round(mean, 1),
                             "target": "Y" if z == info["target"] else "", "label": "", "source": "auto"})
        summary = ", ".join(f"{z} {max((b - a_ for _, a_, b, _ in v), default=0):.1f}s" for z, v in segs.items() if v)
        print(f"{info['file']:34s} 점 평균 전체 {feat['n_all'].mean():5.0f} / 칸 안 {feat['n_in'].mean():4.0f}  최장 머묾: {summary or '없음'}")
        if not a.no_plot:
            draw_review(p, info, feat, segs, st, a, out / "review" / f"{p.stem}.png")

    out.mkdir(parents=True, exist_ok=True)
    keys = ["file", "session", "zone", "start_s", "end_s", "dur_s", "mean_pts", "target", "label", "source"]
    with (out / "labels_auto.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"\n후보 {len(rows)}개 저장: {out / 'labels_auto.csv'}")
    if not a.no_plot:
        print(f"확인용 그림: {out / 'review'}")


def draw_review(pcr, info, feat, segs, st: Stall, a, path):
    plt = setup_fonts()
    import matplotlib.gridspec as gs
    import matplotlib.patches as mp

    zones = st.zone_names()
    fig = plt.figure(figsize=(16, 2 + 1.1 * (len(zones) + 1)))
    g = gs.GridSpec(len(zones) + 1, 3, figure=fig, width_ratios=[3.2, 1, 1])
    t = feat["t"]  # 세션이 여러 개면 시각이 겹쳐 그려진다 (inventory.py에서 세션 수 확인, 대부분 1개)
    ax0 = fig.add_subplot(g[0, 0])
    ax0.plot(t, feat["n_in"], lw=0.8, label="칸 안 점")
    ax0.plot(t, feat["n_move"], lw=0.8, color="tab:red", label=f"칸 안 움직이는 점 (>{a.moving})")
    ax0.legend(loc="upper right", fontsize=7)
    ax0.set_title(f"{info['file']}   [{info['cls']}]   목표 구역: {info['target'] or '-'}", fontsize=10, loc="left")
    for i, z in enumerate(zones):
        ax = fig.add_subplot(g[i + 1, 0], sharex=ax0)
        ax.plot(t, feat["zone_still"][z], lw=0.7, color=ZONE_COLOR.get(z, "k"))
        ax.axhline(a.min_pts, lw=0.6, ls="--", color="k")
        for _, t0, t1, _ in segs[z]:
            ax.axvspan(t0, t1, color=ZONE_COLOR.get(z, "k"), alpha=0.25)
            ax.text(t0, ax.get_ylim()[1] * 0.85 if ax.get_ylim()[1] > 0 else 1, f"{t1 - t0:.1f}s", fontsize=7)
        ax.set_ylabel(z + (" ★" if z == info["target"] else ""), fontsize=8, rotation=0, ha="right", va="center")
        ax.tick_params(labelsize=7)
    ax.set_xlabel("시각 (초)")

    # 오른쪽: 목표 구역(없으면 가장 긴 후보가 있는 구역)의 가장 긴 후보 구간 동안 점
    if a.focus and segs.get(a.focus):
        focus = a.focus
    else:
        focus = info["target"] if segs.get(info["target"]) else max(segs, key=lambda z: max((s[2] - s[1] for s in segs[z]), default=0))
    cand = max(segs.get(focus, []), key=lambda s: s[2] - s[1], default=None)
    if cand is None:
        s0, t0, t1 = 0, float(t.min()), float(t.max())
        sub = "후보 없음: 파일 전체"
    else:
        s0, t0, t1, _ = cand
        sub = f"{focus} 최장 후보 {t0:.1f}~{t1:.1f}s"
    pts = collect(pcr, s0, t0, t1)
    sp = st.to_stall(pts) if len(pts) else pts
    inn = st.inside(sp, 0.0) if len(sp) else np.zeros(0, bool)
    for k, (h, v, lab) in enumerate([(0, 1, ("X 왼쪽→오른쪽", "Y 뒷벽→문")), (1, 2, ("Y 뒷벽→문", "Z 높이"))]):
        ax = fig.add_subplot(g[:, 1 + k])
        if len(sp):
            ax.scatter(sp[~inn, h], sp[~inn, v], s=3, c="#cccccc", marker="x", alpha=0.3)
            ax.scatter(sp[inn, h], sp[inn, v], s=4, c=sp[inn, 3], cmap="coolwarm", vmin=-0.3, vmax=0.3, alpha=0.5)
        w = [st.width, st.depth, st.height]
        ax.add_patch(mp.Rectangle((0, 0), w[h], w[v], fill=False, lw=1.2, color="k"))
        for fx in st.fixtures:
            b = fx["box"]
            ax.add_patch(mp.Rectangle((b[2 * h], b[2 * v]), b[2 * h + 1] - b[2 * h], b[2 * v + 1] - b[2 * v],
                                      fill=False, lw=1, color="tab:orange"))
        for zz in st.zones:
            if zz["name"] == focus and "box" in zz:
                b = zz["box"]
                ax.add_patch(mp.Rectangle((b[2 * h], b[2 * v]), b[2 * h + 1] - b[2 * h], b[2 * v + 1] - b[2 * v],
                                          fill=True, alpha=0.12, color=ZONE_COLOR.get(focus, "k")))
        ax.plot(st.radar_pos[h], st.radar_pos[v], "r^", ms=8)
        ax.set_xlim(-0.3, w[h] + 0.3)
        ax.set_ylim(-0.3, w[v] + 0.3)
        ax.set_aspect("equal")
        ax.set_xlabel(lab[0], fontsize=8)
        ax.set_ylabel(lab[1], fontsize=8)
        ax.set_title(("위에서 " if k == 0 else "옆에서 ") + sub, fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=100)
    plt.close(fig)


def collect(pcr, session, t0, t1):
    names, chunks = [], []
    for sname, _, t, pts in iter_frames(str(pcr)):
        if sname not in names:
            names.append(sname)
        if names.index(sname) == session and t0 <= t <= t1 and len(pts):
            chunks.append(pts)
    return np.concatenate(chunks) if chunks else np.zeros((0, 6), np.float32)


if __name__ == "__main__":
    main()
