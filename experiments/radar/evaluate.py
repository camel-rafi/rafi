r"""RaFi 레이더 처리 - 규칙 v0 평가 (머문 시간 기준 경보)

규칙 v0
  파일(녹화 하나)에서 어떤 구역이든 '붙어 있음'이 θ초 이상 이어지면 경보.
  구역 범위 두 가지를 비교한다.
    fixture : 비품 구역만 (디스펜서, 위생용품 함, 선반 아래)
    any     : 비품 + 벽 4면

평가 (danger_*와 install_vent = 경보가 울려야 하는 녹화, 나머지 = 울리면 안 되는 녹화, cal_can_* = 제외)
  1) 파일별 최장 머문 시간 표                         → eval_files.csv
  2) θ를 0.5초 간격으로 바꾸며 탐지율 / 오경보율      → eval_sweep.csv
  3) 사람 교차 검증: 한 사람 녹화로 θ를 정하고 다른 사람 녹화로 시험
  4) 헷갈리는 쌍 비교: danger_tissue vs tissue_use·normal_full (같은 디스펜서), install_vent vs 일어서기·나가기 (높은 곳) 등
  5) labels.csv가 있으면 사람이 확인한 접촉 구간과 자동 후보를 비교
  그림: eval_dwell.png (동작별 최장 머문 시간, θ 선)

사용 예:
    python experiments/radar/evaluate.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from rafi.radar.analysis_common import (
    add_segment_args,
    apply_zones,
    climb_scores,
    compute_features,
    list_pcr,
    parse_name,
    seg_kw,
    segments_by_session,
    setup_fonts,
)
from rafi.radar.stall import Stall

THETAS = np.round(np.arange(0.5, 20.01, 0.5), 2)
CONTACT_WORDS = {"contact", "y", "yes", "o", "1", "접촉"}


def rates(rows, key, theta):
    d = [r[key] >= theta for r in rows if r["cls"] == "danger"]
    n = [r[key] >= theta for r in rows if r["cls"] == "normal"]
    tpr = float(np.mean(d)) if d else float("nan")
    fpr = float(np.mean(n)) if n else float("nan")
    return tpr, fpr, len(d), len(n)


def pick_theta(rows, key):
    """탐지율 − 오경보율이 가장 큰 θ. 같은 점수의 θ가 여러 개면 오경보가 적은 것 중 가운데 값
    (경계값을 고르면 다른 사람 데이터에서 바로 틀리기 쉬움)."""
    scored = []
    for th in THETAS:
        tpr, fpr, nd, nn = rates(rows, key, th)
        if nd and nn:
            scored.append((round(tpr - fpr, 6), -fpr, th))
    if not scored:
        return None
    top = max(scored)[:2]
    ths = [th for j, f, th in scored if (j, f) == top]
    return float(ths[len(ths) // 2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--out", default=r"out\1102")
    ap.add_argument("--labels", default=r"out\1102\labels.csv", help="사람이 고친 라벨 (없으면 5번 생략)")
    ap.add_argument("--alarm", default=None,
                    help="구역별 경보 기준(초). 하나라도 넘으면 경보. 비우면 stall json의 구역별 alarm_s와 climb.alarm_s를 씀. "
                         "예: dispenser_low=1,sanbox_top=2,under_shelf=5.5,climb=3")
    add_segment_args(ap)
    a = ap.parse_args()

    st = apply_zones(Stall.load(a.stall), a.zones)
    climb_cfg = json.loads(Path(a.stall).read_text(encoding="utf-8")).get("climb", {})
    fixture_zones = [z["name"] for z in st.zones if z.get("kind") == "fixture"]
    out = Path(a.out)
    rows, auto = [], defaultdict(list)
    for p in list_pcr(a.folder):
        info = parse_name(p)
        if info["cls"] == "calib":  # 캔 보정 녹화는 평가에서 뺀다 (캔이 비품 위에 계속 서 있어 '머묾'으로 잡힘)
            continue
        feat = compute_features(p, a.stall, out, a.moving, a.static, zones_path=a.zones)
        if feat["trim"]["frames"]:
            print(f"   [주의] {info['file']}: 앞 {feat['trim']['frames']}프레임이 {feat['trim']['from']}와 똑같아서 잘라냄 (레코더가 세션을 안 비움)")
        r = dict(info)
        for z in st.zone_names():
            segs = segments_by_session(feat, z, **seg_kw(a))
            auto[(info["file"], z)] = segs
            r[z] = round(max((t1 - t0 for _, t0, t1, _ in segs), default=0.0), 2)
        cl = climb_scores(feat, rise=climb_cfg.get("rise", 0.2), min_height=climb_cfg.get("min_height", 0.0))
        r["climb_rise"], r["climb_vanish"] = round(cl["rise"], 2), round(cl["vanish"], 2)
        r["climb"] = max(r["climb_rise"], r["climb_vanish"])
        r["stand_h"] = round(cl["base"], 2) if np.isfinite(cl["base"]) else ""
        r["fixture"] = max(r[z] for z in fixture_zones) if fixture_zones else 0.0
        r["any"] = max(r[z["name"]] for z in st.zones if z.get("kind") in ("fixture", "wall"))  # reference(변기 등)는 경보에서 제외
        r["target_dwell"] = r.get(info["target"], "") if info["target"] else ""
        rows.append(r)

    out.mkdir(parents=True, exist_ok=True)
    keys = (["file", "action", "person", "rep", "cls", "target", "target_dwell", "fixture", "any"] + st.zone_names()
            + ["climb", "climb_rise", "climb_vanish", "stand_h"])
    with (out / "eval_files.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    people = sorted({r["person"] for r in rows} - {"?"})  # 빈 칸·문 흔들림 녹화(사람 없음)는 '전체'에만 넣음
    groups = {"전체": rows, **{p: [r for r in rows if r["person"] == p] for p in people}}

    # 2) θ 스윕
    sweep = []
    for key in ("fixture", "any"):
        for gname, gr in groups.items():
            for th in THETAS:
                tpr, fpr, nd, nn = rates(gr, key, th)
                sweep.append({"zones": key, "group": gname, "theta": th, "tpr": tpr, "fpr": fpr, "n_danger": nd, "n_normal": nn})
    with (out / "eval_sweep.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(sweep[0]))
        w.writeheader()
        w.writerows(sweep)

    print("=" * 70)
    print("1) 동작별 목표 구역 최장 머문 시간 (초)")
    by_act = defaultdict(list)
    for r in rows:
        by_act[r["action"]].append(r)
    print(f"{'동작':26s} {'목표구역':12s} {'파일':>4s} {'목표구역 머묾':>22s} {'any 최장':>10s}")
    for act, rs in sorted(by_act.items()):
        tz = rs[0]["target"]
        td = [x["target_dwell"] for x in rs if tz]
        tds = f"{np.min(td):.1f} / {np.mean(td):.1f} / {np.max(td):.1f}" if td else "-"
        print(f"{act:26s} {tz or '-':12s} {len(rs):4d} {tds:>22s} {np.mean([x['any'] for x in rs]):10.1f}")
    print("   (목표구역 머묾 = 최소 / 평균 / 최대)")

    print("\n2) θ별 탐지율 / 오경보율 (전체)")
    for key in ("fixture", "any"):
        line = [f"θ={th:>4}: {rates(rows, key, th)[0]:.2f}/{rates(rows, key, th)[1]:.2f}" for th in (1, 2, 3, 5, 8)]
        print(f"   {key:8s} " + "   ".join(line))

    print("\n3) 사람 교차 검증 (한 사람으로 θ 결정 → 다른 사람으로 시험)")
    cross = {}
    for key in ("fixture", "any"):
        for tr in people:
            th = pick_theta(groups[tr], key)
            for te in people:
                if te == tr or th is None:
                    continue
                tpr, fpr, nd, nn = rates(groups[te], key, th)
                cross[(key, tr)] = th
                print(f"   {key:8s} {tr}로 θ={th:.1f}s 결정 → {te} 시험: 탐지율 {tpr:.2f} ({nd}개), 오경보율 {fpr:.2f} ({nn}개)")

    print("\n4) 헷갈리는 쌍")
    for d_act, n_act, zone in [("danger_tissue", "tissue_use", "dispenser_low"),
                               ("danger_tissue", "normal_full", "dispenser_low"),
                               ("danger_tissue_sitting", "tissue_use", "dispenser_low"),
                               ("danger_under_shelve", "normal_full", "under_shelf"),
                               ("danger_under_shelve", "pad_bin", "any"),
                               ("danger_install_vent", "normal_full", "high"),
                               ("danger_install_vent", "exiting_closed_door", "high"),
                               ("danger_install_vent", "shaking_door", "any"),
                               ("danger_install_vent", "normal_full", "climb"),
                               ("danger_install_vent", "exiting_closed_door", "climb"),
                               ("danger_install_vent", "sitting_still", "climb")]:
        dv = [r.get(zone, 0) for r in by_act.get(d_act, [])]
        nv = [r.get(zone, 0) for r in by_act.get(n_act, [])]
        if dv and nv:
            sep = "갈림" if min(dv) > max(nv) else "겹침"
            print(f"   {zone:11s} {d_act:22s} {fmt(dv)}  vs  {n_act:12s} {fmt(nv)}  → {sep}")

    if a.alarm:
        rule = {k: float(v) for k, v in (x.split("=") for x in a.alarm.split(","))}
    else:
        rule = {z["name"]: float(z["alarm_s"]) for z in st.zones if "alarm_s" in z}
        if "alarm_s" in climb_cfg:
            rule["climb"] = float(climb_cfg["alarm_s"])
    print(f"\n6) 구역별 기준 경보: {', '.join(f'{k} ≥ {v}s' for k, v in rule.items())} 중 하나라도 넘으면 경보")
    hit = lambda r: any(r.get(k, 0) >= v for k, v in rule.items())  # noqa: E731
    for gname, gr in groups.items():
        d = [r for r in gr if r["cls"] == "danger"]
        n = [r for r in gr if r["cls"] == "normal"]
        print(f"   {gname:4s} 위험 탐지 {sum(map(hit, d))}/{len(d)}, 정상 오경보 {sum(map(hit, n))}/{len(n)}")
    print("   놓친 위험:", [r["file"] for r in rows if r["cls"] == "danger" and not hit(r)])
    print("   오경보:", [r["file"] for r in rows if r["cls"] == "normal" and hit(r)])
    why = lambda r: ", ".join(f"{k} {r.get(k, 0):.1f}s" for k, v in rule.items() if r.get(k, 0) >= v)  # noqa: E731
    for r in rows:
        if hit(r):
            print(f"      {'위험' if r['cls'] == 'danger' else '오경보'} {r['file']:32s} ← {why(r)}")
    near = sorted(((max(r.get(k, 0) / v for k, v in rule.items()), r) for r in rows if r["cls"] == "normal" and not hit(r)),
                  key=lambda x: -x[0])[:5]
    print("   경보는 안 났지만 기준에 가까운 정상 녹화 (기준 대비 비율):")
    for ratio, r in near:
        k = max(rule, key=lambda kk: r.get(kk, 0) / rule[kk])
        print(f"      {r['file']:32s} {k} {r.get(k, 0):.1f}s / 기준 {rule[k]:.1f}s ({ratio:.0%})")

    print("\n7) 높은 곳(올라섬): 처음 서 있던 높이 / 상승 최장 / 사라짐 최장 (초)")
    for r in rows:
        if r["action"] == "install_vent" or r["climb"] >= 1.0:
            print(f"   {r['file']:32s} [{r['cls']:6s}] 서 있던 높이 {r['stand_h'] or '-':>5}  상승 {r['climb_rise']:5.1f}  사라짐 {r['climb_vanish']:5.1f}")

    lab = Path(a.labels)
    if lab.exists():
        compare_labels(lab, auto)
    else:
        print(f"\n5) {lab} 없음: 자동 후보를 확인해 labels.csv를 만들면 구간 단위 평가도 함")

    draw(rows, cross, out / "eval_dwell.png")
    print(f"\n저장: {out / 'eval_files.csv'}, {out / 'eval_sweep.csv'}, {out / 'eval_dwell.png'}")


def fmt(v):
    return "[" + ", ".join(f"{x:.1f}" for x in sorted(v)) + "]"


def compare_labels(path, auto):
    gt = defaultdict(list)
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("label", "").strip().lower() in CONTACT_WORDS:
                gt[(r["file"], r["zone"])].append((int(r.get("session") or 0), float(r["start_s"]), float(r["end_s"])))
    n = hit = 0
    errs = []
    for key, segs in gt.items():
        for s, a0, a1 in segs:
            n += 1
            best = 0.0
            bdur = None
            for s2, b0, b1, _ in auto.get(key, []):
                if s2 != s:
                    continue
                inter = max(0.0, min(a1, b1) - max(a0, b0))
                iou = inter / (max(a1, b1) - min(a0, b0))
                if iou > best:
                    best, bdur = iou, b1 - b0
            if best >= 0.3:
                hit += 1
                errs.append(bdur - (a1 - a0))
    print(f"\n5) 사람이 확인한 접촉 구간 {n}개 중 자동 후보와 겹친 것 (IoU ≥ 0.3): {hit}개")
    if errs:
        e = np.array(errs)
        print(f"   자동 머문 시간 − 실제: 평균 {e.mean():+.2f}s, 절댓값 평균 {np.abs(e).mean():.2f}s")


def draw(rows, cross, path):
    plt = setup_fonts()
    acts = sorted({r["action"] for r in rows}, key=lambda x: (not x.startswith("danger"), x))
    fig, ax = plt.subplots(1, 2, figsize=(14, 0.45 * len(acts) + 2), sharey=True)
    marker = {p: m for p, m in zip(sorted({r["person"] for r in rows}), "os^Dv", strict=False)}
    for k, key in enumerate(("fixture", "any")):
        axis = ax[k]
        for i, act in enumerate(acts):
            for r in rows:
                if r["action"] == act:
                    axis.scatter(r[key], i + (0.12 if r["person"] == sorted(marker)[0] else -0.12),
                                 marker=marker[r["person"]], s=36,
                                 color="tab:red" if r["cls"] == "danger" else "tab:blue", alpha=0.8)
        for j, ((_kk, tr), th) in enumerate((k_, v) for k_, v in cross.items() if k_[0] == key):
            axis.axvline(th, ls="--", lw=1, color="k")
            axis.text(th, len(acts) - 0.6 - 0.35 * j, f" θ({tr})={th:.1f}s", fontsize=8)
        axis.set_yticks(range(len(acts)))
        axis.set_yticklabels(acts, fontsize=8)
        axis.set_xlabel("최장 머문 시간 (초)")
        axis.set_title(f"구역: {key}   (빨강 = danger, 파랑 = 정상, 모양 = 사람 {marker})", fontsize=9)
        axis.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    main()
