r"""RaFi 실시간 탐지기 평가: 녹화를 빠르게 재생해서 실시간 규칙 그대로 돌린다.

계획서 지표에 맞춰 잰다.
  - 탐지율: 위험 녹화 중 경보(radar.detection)가 한 번이라도 나간 비율
  - 시간당 오탐: 정상 녹화 전체 시간 동안 나간 경보 사건 수 ÷ 시간
  - 경보 지연: 머묾(또는 올라섬)이 시작된 시각 → 첫 경보를 보낸 시각 (녹화에 손 댄 시각 라벨이 없어서 이것으로 대신함)

사용 예:
    python experiments/radar/rt_eval.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from rafi.radar.analysis_common import background_codes, list_pcr, parse_name, prefix_trim
from rafi.radar.detector import Detector, sim_clock
from rafi.radar.messages import KST
from rafi.radar.stream import ReplaySource


def _run_one(job):
    """녹화 하나를 실시간 규칙으로 재생 → (결과 행, 길이[초])."""
    p, stall, bg, out = job
    p = Path(p)
    info = parse_name(p)
    start = datetime(2026, 11, 2, 12, 0, tzinfo=KST)
    skip = prefix_trim(p, Path(out))["frames"]
    det = Detector(stall, bg, clock=sim_clock(start))
    first = {}
    rules: dict[str, float] = {}  # (2026-11-12) 판단(구역·머리 등)별 처음 기준을 넘은 시각. 사건으로 합쳐져도 따로 셈
    rule_n: dict[str, set] = defaultdict(set)  # 판단별 따로 시작된 횟수
    t0 = t1 = None
    for t, pts in ReplaySource(p, speed=0, skip_frames=skip):
        t0 = t if t0 is None else t0
        t1 = t
        for m in det.step(t, pts):
            pl = m["payload"]
            if pl["event_id"] not in first:
                sent = datetime.fromisoformat(m["sent_at"])
                es = datetime.fromisoformat(pl["event_start"])
                first[pl["event_id"]] = {"roi": pl["roi"], "alarm_at_s": (sent - start).total_seconds() - t0,
                                         "delay_s": (sent - es).total_seconds(), "reason": pl["reasons"][0]}
        for key, ev in list(det.events.items()) + list(det.ended.items()):
            if ev.alarmed:
                rules.setdefault(key, round(t - t0, 1))
                rule_n[key].add(id(ev))
    dur = (t1 - t0) if t0 is not None else 0.0
    evs = list(first.values())
    row = {**info, "dur_s": round(dur, 1), "n_events": len(evs),
           "events": "; ".join(f"{e['roi']} @{e['alarm_at_s']:.1f}s (지연 {e['delay_s']:.1f}s)" for e in evs),
           "first_alarm_s": round(min((e["alarm_at_s"] for e in evs), default=np.nan), 1),
           "delays": [e["delay_s"] for e in evs], "reasons": [e["reason"] for e in evs],
           "rules": "; ".join(f"{k}@{v:.1f}s" + (f" x{len(rule_n[k])}" if len(rule_n[k]) > 1 else "")
                              for k, v in sorted(rules.items(), key=lambda x: x[1])),
           "rule_set": sorted(rules)}
    return row, dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--out", default=r"out\rt")
    ap.add_argument("--only", default="*.pcr")
    ap.add_argument("--jobs", type=int, default=1, help="동시에 돌릴 프로세스 수 (2026-11-12 추가)")
    a = ap.parse_args()

    out = Path(a.out)
    bg = background_codes(a.stall, a.folder, out)
    rows, normal_sec = [], 0.0
    files = [p for p in list_pcr(a.folder, a.only) if parse_name(p)["cls"] != "calib"]
    for p in files:  # 앞에 붙은 녹화 해시를 먼저 만들어 둠 (병렬 실행 때 캐시 파일을 동시에 쓰지 않게)
        prefix_trim(p, out)
    jobs = [(str(p), a.stall, bg, str(out)) for p in files]
    if a.jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(a.jobs) as ex:
            results = list(ex.map(_run_one, jobs))
    else:
        results = [_run_one(j) for j in jobs]
    for row, dur in results:
        if row["cls"] == "normal":
            normal_sec += dur
        rows.append(row)
        tag = "위험" if row["cls"] == "danger" else "정상"
        print(f"{row['file']:32s} [{tag}] {dur:5.1f}s  경보 {row['n_events']}건  {row['events']}")
        if row["rules"]:
            print(f"{'':34s}켜진 판단 전부: {row['rules']}")

    d = [r for r in rows if r["cls"] == "danger"]
    n = [r for r in rows if r["cls"] == "normal"]
    hit = [r for r in d if r["n_events"]]
    fa = sum(r["n_events"] for r in n)
    delays = [x for r in rows for x in r["delays"]]
    print("\n" + "=" * 70)
    print(f"탐지율: {len(hit)}/{len(d)}  (놓침: {[r['file'] for r in d if not r['n_events']]})")
    print(f"정상 녹화 {len(n)}개, 총 {normal_sec / 60:.1f}분 동안 오탐 {fa}건 → 시간당 {fa / max(normal_sec / 3600, 1e-9):.1f}건")
    print(f"   오탐 녹화: {[(r['file'], r['events']) for r in n if r['n_events']]}")
    if delays:
        print(f"경보 지연 (머묾 시작 → 첫 경보): 중앙값 {np.median(delays):.1f}s, 최대 {np.max(delays):.1f}s")
    by_roi = defaultdict(int)
    for r in d:
        for e in r["events"].split("; "):
            if e:
                by_roi[e.split(" @")[0]] += 1
    print("위험 녹화 경보 구역별 건수:", dict(by_roi))
    # (2026-11-12) 판단별: 첫 이유만 보면 합쳐진 판단이 가려진다 → 켜진 판단 전부 기준으로 센다
    rule_d, rule_n_, only = defaultdict(int), defaultdict(int), defaultdict(list)
    for r in d:
        for k in r["rule_set"]:
            rule_d[k] += 1
        if len(r["rule_set"]) == 1:
            only[r["rule_set"][0]].append(r["file"][:-4])
    for r in n:
        for k in r["rule_set"]:
            rule_n_[k] += 1
    print("판단별 (켜진 판단 전부 기준):")
    for k in sorted(set(rule_d) | set(rule_n_), key=lambda k: -rule_d.get(k, 0)):
        print(f"   {k:14s} 위험 녹화 {rule_d.get(k, 0):3d}개 | 정상 녹화 {rule_n_.get(k, 0):2d}개 | 이 판단 하나로만 잡힌 위험 {len(only.get(k, []))}개"
              + (f" {only[k]}" if 0 < len(only.get(k, [])) <= 12 else ""))
    groups = defaultdict(lambda: [0, 0, defaultdict(int)])
    for r in d:
        g = r.get("action") or r["file"]
        groups[g][0] += 1
        groups[g][1] += bool(r["n_events"])
        for k in r["rule_set"]:
            groups[g][2][k] += 1
    print("동작별 검출과 켜진 판단 (녹화 수):")
    for g, (tot, h, ks) in sorted(groups.items()):
        print(f"   {g:28s} {h}/{tot}  " + ", ".join(f"{k} {c}" for k, c in sorted(ks.items(), key=lambda x: -x[1])))
    print("첫 경보까지 녹화 시작부터 걸린 시간 (위험):",
          ", ".join(f"{r['file'][:-4]} {r['first_alarm_s']}s" for r in hit))

    out.mkdir(parents=True, exist_ok=True)
    with (out / "rt_eval.csv").open("w", newline="", encoding="utf-8-sig") as f:
        keys = ["file", "action", "person", "cls", "dur_s", "n_events", "first_alarm_s", "events", "rules", "reasons"]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n저장: {out / 'rt_eval.csv'}")


if __name__ == "__main__":
    main()
