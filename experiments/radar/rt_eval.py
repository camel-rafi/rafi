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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--stall", default="config/radar/stall_1102.json")
    ap.add_argument("--out", default=r"out\rt")
    ap.add_argument("--only", default="*.pcr")
    a = ap.parse_args()

    out = Path(a.out)
    bg = background_codes(a.stall, a.folder, out)
    start = datetime(2026, 11, 2, 12, 0, tzinfo=KST)
    rows, normal_sec = [], 0.0
    for p in list_pcr(a.folder, a.only):
        info = parse_name(p)
        if info["cls"] == "calib":
            continue
        skip = prefix_trim(p, out)["frames"]
        det = Detector(a.stall, bg, clock=sim_clock(start))
        first = {}
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
        dur = (t1 - t0) if t0 is not None else 0.0
        if info["cls"] == "normal":
            normal_sec += dur
        evs = list(first.values())
        rows.append({**info, "dur_s": round(dur, 1), "n_events": len(evs),
                     "events": "; ".join(f"{e['roi']} @{e['alarm_at_s']:.1f}s (지연 {e['delay_s']:.1f}s)" for e in evs),
                     "first_alarm_s": round(min((e["alarm_at_s"] for e in evs), default=np.nan), 1),
                     "delays": [e["delay_s"] for e in evs], "reasons": [e["reason"] for e in evs]})
        tag = "위험" if info["cls"] == "danger" else "정상"
        print(f"{info['file']:32s} [{tag}] {dur:5.1f}s  경보 {len(evs)}건  {rows[-1]['events']}")

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
    print("첫 경보까지 녹화 시작부터 걸린 시간 (위험):",
          ", ".join(f"{r['file'][:-4]} {r['first_alarm_s']}s" for r in hit))

    out.mkdir(parents=True, exist_ok=True)
    with (out / "rt_eval.csv").open("w", newline="", encoding="utf-8-sig") as f:
        keys = ["file", "action", "person", "cls", "dur_s", "n_events", "first_alarm_s", "events"]
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n저장: {out / 'rt_eval.csv'}")


if __name__ == "__main__":
    main()
