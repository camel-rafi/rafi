r"""RaFi 레이더 처리 - 녹화 폴더 전체 요약표

폴더 안 .pcr 파일마다 이름 라벨, 세션 수, 길이, fps, 프레임당 점 개수, 헤더의 센서 자세를 한 줄로 정리한다.
깨진 파일, 너무 짧은 파일, 세션이 여러 개인 파일을 먼저 걸러 내는 용도.

사용 예:
    python experiments/radar/inventory.py ..\sg-radar-tools\recordings\record_1102 --out out\1102\inventory.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

import numpy as np

from rafi.radar.analysis_common import list_pcr, parse_name
from rafi.radar.radar_io import iter_frames

try:
    from rafi.radar.radar_io import load  # 헤더(센서 자세)를 읽기 위해. 없으면 건너뜀
except ImportError:  # pragma: no cover
    load = None


def header_pose(path) -> str:
    if load is None:
        return ""
    try:
        s = load(str(path)).header.sensor_spec
        return f"({s.pos_x:.2f},{s.pos_y:.2f},{s.pos_z:.2f}) yaw {s.yaw_deg:.0f} pitch {s.pitch_deg:.0f}"
    except Exception as e:  # noqa: BLE001
        return f"헤더 읽기 실패: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=r"out\1102\inventory.csv")
    ap.add_argument("--short", type=float, default=5.0, help="이보다 짧은 녹화는 경고(초)")
    a = ap.parse_args()

    rows = []
    for p in list_pcr(a.folder):
        info = parse_name(p)
        try:
            ts, sess, npts = [], [], []
            for sname, _, t, pts in iter_frames(str(p)):
                ts.append(t)
                sess.append(sname)
                npts.append(len(pts))
            ok = True
        except Exception as e:  # noqa: BLE001
            ok, err = False, str(e)
        if not ok or not ts:
            rows.append({**info, "status": f"읽기 실패: {err}" if not ok else "프레임 없음"})
            print(f"[실패] {p.name}")
            continue
        n_sess = len(dict.fromkeys(sess))
        # 세션이 여러 개면 세션마다 t가 0부터 다시 시작하므로 세션별 마지막 t를 더한다
        last = {}
        for s, t in zip(sess, ts, strict=False):
            last[s] = t
        dur = sum(last.values())
        npts = np.array(npts)
        status = []
        if dur < a.short:
            status.append("짧음")
        if n_sess > 1:
            status.append(f"세션 {n_sess}개")
        rows.append({
            **info,
            "sessions": n_sess,
            "duration_s": round(dur, 1),
            "frames": len(ts),
            "fps": round(len(ts) / dur, 1) if dur > 0 else "",
            "pts_mean": int(npts.mean()),
            "pts_max": int(npts.max()),
            "header_pose": header_pose(p),
            "status": " / ".join(status) or "ok",
        })

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    keys = ["file", "action", "person", "rep", "cls", "target", "sessions", "duration_s", "frames", "fps",
            "pts_mean", "pts_max", "header_pose", "status"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig: 엑셀에서 한글 안 깨짐
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\n{'파일':34s} {'길이':>6s} {'fps':>5s} {'점평균':>6s}  상태")
    for r in rows:
        print(f"{r['file']:34s} {r.get('duration_s', ''):>6} {r.get('fps', ''):>5} {r.get('pts_mean', ''):>6}  {r['status']}")

    print("\n동작 × 사람 개수")
    cnt = Counter((r["action"], r["person"]) for r in rows)
    people = sorted({r["person"] for r in rows})
    print(f"{'동작':26s}" + "".join(f"{p:>5s}" for p in people))
    for act in sorted({r["action"] for r in rows}):
        print(f"{act:26s}" + "".join(f"{cnt[(act, p)]:>5d}" for p in people))
    poses = Counter(r.get("header_pose", "") for r in rows)
    print("\n헤더 센서 자세:", dict(poses))
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
