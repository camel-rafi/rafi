r"""RaFi 레이더 처리 - 1102 녹화 분석 공통 함수

- 파일 이름에서 라벨 읽기:  동작_사람_번호.pcr  (예: danger_tissue_kb_1.pcr)
- 프레임마다 구역별 점 개수 계산 (결과는 out\features 에 저장해 두고 다시 씀)
- 구역에 점이 일정 시간 이상 머문 구간 찾기
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np

from .radar_io import iter_frames
from .stall import Stall

DOPPLER_BIN = 0.0724  # m/s, 1차 분석에서 확인한 도플러 한 칸

# 동작 이름 → 손이 가는 구역 (리뷰 그림에서 강조할 구역)
TARGET_ZONE = {
    "danger_tissue": "dispenser_low",  # 디스펜서 앞(레이더 쪽 면)·아래에 손을 둠
    "danger_tissue_sitting": "dispenser_low",
    "tissue_use": "dispenser_low",  # 휴지는 디스펜서 아래로 나옴
    "normal_full": "dispenser_low",  # 전체 사용 중에 휴지 사용이 들어 있음
    "danger_under_shelve": "under_shelf",
    "pad_bin": "sanbox_top",  # 투입구가 위쪽
    "install_vent": "high",
    "danger_install_vent": "high",  # 2026-11-10 녹화 이름 앞에 danger_를 붙임
}

# 위험 동작 (경보가 울려야 하는 녹화). danger_로 시작하는 것 + 아래 목록
DANGER_ACTIONS = {"install_vent"}  # 예전 이름. 11-10부터 녹화는 danger_install_vent_*


def classify(action: str) -> str:
    """danger: 경보가 울려야 함 / normal: 울리면 안 됨 (빈 칸, 문 흔들림 포함) / calib: 평가에서 제외 (캔 보정)"""
    if action.startswith(("cal_", "excluded_")):  # 보정 녹화, 잘못 찍은 녹화(2026-11-10: excluded_ 접두어)
        return "calib"
    if action.startswith("danger") or action in DANGER_ACTIONS:
        return "danger"
    return "normal"

# 반복 번호는 _1 또는 바로 붙은 1 모두 받음 (install_vent_hs1 → install_vent, hs, 1). 2026-11-10: 예전에는 hs1을 못 읽어 install_vent_hs1·hs2가 정상으로 분류됐음
NAME_RE = re.compile(r"^(?P<action>.+?)_(?P<person>[a-z]{2})(?:_?(?P<rep>\d+))?$")


def parse_name(path) -> dict:
    """danger_tissue_kb_1.pcr → action=danger_tissue, person=kb, rep=1, cls=danger"""
    stem = Path(path).stem
    m = NAME_RE.match(stem)
    if m:
        action, person, rep = m["action"], m["person"], int(m["rep"] or 1)
    else:  # 사람 표시가 없는 녹화 (빈 칸, 캔 보정, shaking_door_1 등)
        m2 = re.match(r"^(?P<action>.+?)(?:_(?P<rep>\d+))?$", stem)
        action, person, rep = m2["action"], "?", int(m2["rep"] or 1)
    return {
        "file": Path(path).name,
        "action": action,
        "person": person,
        "rep": rep,
        "cls": classify(action),
        "target": TARGET_ZONE.get(action, ""),
    }


def list_pcr(folder, pattern="*.pcr") -> list[Path]:
    files = sorted(Path(folder).glob(pattern))
    if not files:
        raise SystemExit(f"{folder} 에서 {pattern} 파일을 찾지 못함")
    return files


def setup_fonts():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    have = {f.name for f in font_manager.fontManager.ttflist}
    # 있는 한글 글꼴 하나만 지정 (없는 글꼴을 목록에 넣으면 findfont 경고가 수백 줄 나옴)
    plt.rcParams["font.family"] = next((f for f in ["Malgun Gothic", "AppleGothic", "NanumGothic"] if f in have), "DejaVu Sans")
    plt.rcParams["axes.unicode_minus"] = False
    return plt


# ---------------------------------------------------------------- 프레임 특징

FEATURE_VERSION = 6
TOP_DEPTH_MAX = 0.85  # 머리 높이를 잴 때 뒷벽에서 이 거리(m)까지만 본다. 문 쪽은 문 위쪽 번짐(약 2.0 m)이 섞임
DOOR_MOVE = 0.3  # 문 범위(door_swing)에서 이보다 빠른 점 = 문이 움직이는 중
TREMOR_BIN = 2  # 손을 대고 버틸 때 떨림이 주로 찍히는 도플러 칸 (2칸 ≈ 0.14 m/s). 걸친 팔꿈치·몸은 0칸이 많음


# ---------------------------------------------------------------- 구역
# 구역은 두 종류
#   stall_box    : 칸 좌표 상자 [x0, x1, y0, y1, z0, z1]. 레이더 자세(stall_1102.json)로 점을 칸 좌표로 바꿔 판정
#   radar_voxels : 레이더 좌표의 10 cm 칸 목록. learn_zones.py가 정상 녹화에서 데이터로 정함. 자세가 필요 없음

def voxel_codes(xyz, vox):
    """레이더 좌표 → 칸 번호를 정수 하나로 (빠른 포함 검사용)."""
    k = np.floor(np.asarray(xyz) / vox).astype(np.int64) + 512
    return (k[:, 0] * 1024 + k[:, 1]) * 1024 + k[:, 2]


def voxel_rates(path, vox=0.10, every=1):
    """레이더 좌표 10 cm 칸마다 점이 찍힌 프레임 비율 (도플러로 거르지 않음). 반환: {칸 번호: 비율}"""
    counts, n = {}, 0
    for i, (_, _, _, pts) in enumerate(iter_frames(str(path))):
        if i % every:
            continue
        n += 1
        if len(pts):
            for c in np.unique(voxel_codes(pts[:, :3], vox)).tolist():
                counts[c] = counts.get(c, 0) + 1
    return {c: k / max(n, 1) for c, k in counts.items()}


def code_to_xyz(codes, vox):
    """칸 번호 → 칸 중심 (레이더 좌표)."""
    codes = np.asarray(codes, dtype=np.int64)
    k = np.stack([codes // (1024 * 1024), (codes // 1024) % 1024, codes % 1024], axis=1) - 512
    return (k + 0.5) * vox


def load_zones(path) -> list[dict]:
    """learn_zones.py가 만든 구역 파일 읽기."""
    zones = json.loads(Path(path).read_text(encoding="utf-8"))["zones"]
    for z in zones:
        if z.get("mode") == "radar_voxels":
            z["_codes"] = voxel_codes(np.array(z["voxels"], float) * z["vox"] + z["vox"] / 2, z["vox"])
    return zones


def apply_zones(st: Stall, zones_path) -> Stall:
    """--zones를 주면 stall_1102.json의 구역 대신 그 파일의 구역을 쓴다."""
    if zones_path:
        st.zones = load_zones(zones_path)
    return st


def _stall_key(stall_path, moving, static, zones_path=None) -> str:
    raw = Path(stall_path).read_bytes() + json.dumps([moving, static, FEATURE_VERSION]).encode()
    if zones_path:
        raw += Path(zones_path).read_bytes()
    return hashlib.md5(raw).hexdigest()[:10]


def zone_mask(st: Stall, z, pts, sp, inn):
    if z.get("mode") == "radar_voxels":
        return np.isin(voxel_codes(pts[:, :3], z["vox"]), z["_codes"])
    return inn & st.in_box(sp, z["box"])


def background_codes(stall_path, rec_folder, out_dir, vox=0.10, min_rate=0.10):
    """빈 칸 녹화(stall_1102.json의 empty_files)에서 프레임의 10% 이상 찍힌 10 cm 칸 = 배경.
    사람 녹화에서 이 칸의 점은 뺀다 (늘 같은 자리에 찍히는 벽·문 반사, 허상)."""
    cfg = json.loads(Path(stall_path).read_text(encoding="utf-8"))
    names = cfg.get("empty_files", [])
    if not names:
        return np.zeros(0, dtype=np.int64)
    key = hashlib.md5(json.dumps([names, vox, min_rate, str(Path(rec_folder).resolve())]).encode()).hexdigest()[:10]
    cache = Path(out_dir) / "features" / f"_background.{key}.npy"
    if cache.exists():
        return np.load(cache)
    bg = {}
    for n in names:
        f = Path(rec_folder) / f"{n}.pcr"
        if f.exists():
            for c, r in voxel_rates(f, vox).items():
                bg[c] = max(bg.get(c, 0.0), r)
    codes = np.array(sorted(c for c, r in bg.items() if r >= min_rate), dtype=np.int64)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache, codes)
    return codes


N_HEAD = 6  # 구역 앞의 값: 전체, 칸 안, 칸 안 움직임, 머리 높이(mm), 문 움직임 점, 몸 중심 앞뒤 위치(mm)
N_PER_ZONE = 4  # 구역마다: 전체, 느린 점, 떨림 점, 구역 점 높이 중앙값(mm)


def frame_features(st: Stall, pts: np.ndarray, moving: float, slow: float, bg=None, door_box=None) -> list[float]:
    """프레임 하나 → [전체, 칸 안, 칸 안 움직임, 머리 높이(mm, 없으면 -1), 문 움직임 점, 몸 중심 앞뒤(mm, 없으면 -1),
                    구역1 전체, 구역1 느린 점, 구역1 떨림 점, 구역1 점 높이(mm), 구역2 ...]
    배경 칸의 점은 먼저 뺀다. 느린 점 = |doppler| ≤ slow (구역 설정에 "count": "all"이면 모든 점)
    머리 높이 = 칸 안(뒷벽에서 TOP_DEPTH_MAX까지, 바닥 0.3 m 위) 가장 높은 점 5개의 중앙값. 점이 10개 미만이면 -1
    떨림 점 = 도플러 TREMOR_BIN칸(≈ 0.14 m/s)인 점"""
    nz = len(st.zones)
    if len(pts) and bg is not None and len(bg):
        pts = pts[~np.isin(voxel_codes(pts[:, :3], 0.10), bg)]
    if not len(pts):
        return [0, 0, 0, -1, 0, -1] + [0, 0, 0, -1] * nz
    sp = st.to_stall(pts)
    inn = st.inside(sp, margin=0.0)
    d = np.abs(pts[:, 3])
    body = inn & (sp[:, 1] <= TOP_DEPTH_MAX) & (sp[:, 2] >= 0.3)
    top = int(round(1000 * np.median(np.sort(sp[body, 2])[-5:]))) if body.sum() >= 10 else -1
    trem = np.round(d / DOPPLER_BIN) == TREMOR_BIN
    door = int((st.in_box(sp, door_box) & (d > DOOR_MOVE)).sum()) if door_box else 0
    body_y = int(round(1000 * np.median(sp[inn, 1]))) if inn.sum() >= 10 else -1
    row = [len(pts), int(inn.sum()), int((inn & (d > moving)).sum()), top, door, body_y]
    for z in st.zones:
        m = zone_mask(st, z, pts, sp, inn)
        use = m if z.get("count") == "all" else (m & (d <= slow))
        zh = int(round(1000 * np.median(sp[m, 2]))) if m.any() else -1
        row += [int(m.sum()), int(use.sum()), int((m & trem).sum()), zh]
    return row


def compute_features(pcr, stall_path, out_dir, moving=0.4, static=0.15, use_cache=True, zones_path=None) -> dict:
    """파일 하나의 프레임별 특징. 같은 설정으로 계산한 결과가 있으면 다시 읽는다.
    static = 느린 점 기준 (|doppler| ≤ 이 값, 기본 0.15 m/s = 도플러 0 ~ 2칸)

    반환: t, session, n_all, n_in, n_move, zone_all[구역], zone_still[구역](느린 점), zones
    """
    st = apply_zones(Stall.load(stall_path), zones_path)
    key = _stall_key(stall_path, moving, static, zones_path)
    cache = Path(out_dir) / "features" / f"{Path(pcr).stem}.{key}.npz"
    trim = prefix_trim(pcr, out_dir)
    if use_cache and cache.exists():
        z = np.load(cache, allow_pickle=False)
        return _trim(_unpack(z, st), trim)

    bg = background_codes(stall_path, Path(pcr).parent, out_dir)
    _cfg = json.loads(Path(stall_path).read_text(encoding="utf-8"))
    door_box = (_cfg.get("door_plane") or _cfg.get("door_swing", {})).get("box")  # 문 움직임: 문짝 앞뒤 띠
    rows, ts, ss, names = [], [], [], []
    for sname, _, t, pts in iter_frames(str(pcr)):
        if sname not in names:
            names.append(sname)
        ss.append(names.index(sname))
        ts.append(t)
        rows.append(frame_features(st, pts, moving, static, bg, door_box))
    arr = np.array(rows, dtype=np.int32).reshape(len(rows), N_HEAD + N_PER_ZONE * len(st.zones))
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, t=np.array(ts, dtype=np.float64), session=np.array(ss, dtype=np.int16), arr=arr)
    return _trim(_unpack({"t": np.array(ts), "session": np.array(ss), "arr": arr}, st), trim)


# ---------------------------------------------------------------- 앞 녹화가 붙은 파일 (레코더 버그)
# radar-studio는 Save를 눌러도 세션을 비우지 않는다. 저장 뒤 같은 세션에 이어 녹화하면, 다음 파일 앞부분에
# 직전 녹화가 통째로 들어간다 (1102: pad_bin_kb_3 → danger_tissue_kb_1 앞 6.0초, emptu_closeddoor_opentioliet →
# normal_full_kb_1 앞 30.6초, entering_kb_4 → exiting_closed_door_kb_1 앞 8.4초).
# 같은 폴더의 다른 파일 프레임 전체가 이 파일 맨 앞과 똑같으면 그만큼 잘라낸다.

def _frame_hashes(path, out_dir) -> list[str]:
    path = Path(path)
    cache = Path(out_dir) / "features" / "_hashes.json"
    db = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    key = f"{path.name}|{path.stat().st_size}"
    if key not in db:
        db[key] = [hashlib.md5(np.ascontiguousarray(p, dtype=np.float32).tobytes()).hexdigest()[:12]
                   for _, _, _, p in iter_frames(str(path))]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(db), encoding="utf-8")
    return db[key]


def prefix_trim(pcr, out_dir) -> dict:
    """반환: {"frames": 잘라낼 프레임 수, "from": 앞에 붙은 파일 이름} (없으면 frames 0)"""
    pcr = Path(pcr)
    me = _frame_hashes(pcr, out_dir)
    best = {"frames": 0, "from": ""}
    for other in sorted(pcr.parent.glob("*.pcr")):
        if other == pcr:
            continue
        h = _frame_hashes(other, out_dir)
        if 5 <= len(h) < len(me) and me[:len(h)] == h and len(h) > best["frames"]:
            best = {"frames": len(h), "from": other.name}
    return best


def _trim(feat: dict, trim: dict) -> dict:
    k = trim["frames"]
    feat["trim"] = trim
    if not k:
        return feat
    for key in ("t", "session", "n_all", "n_in", "n_move", "top", "door_move", "body_y"):
        feat[key] = feat[key][k:]
    for key in ("zone_all", "zone_still", "zone_trem", "zone_h"):
        feat[key] = {z: v[k:] for z, v in feat[key].items()}
    return feat


def _unpack(z, st: Stall) -> dict:
    arr = z["arr"]
    def mm(col):
        v = col.astype(float) / 1000
        v[col < 0] = np.nan
        return v

    out = {"t": z["t"], "session": z["session"], "n_all": arr[:, 0], "n_in": arr[:, 1], "n_move": arr[:, 2],
           "top": mm(arr[:, 3]), "door_move": arr[:, 4], "body_y": mm(arr[:, 5]),
           "zones": st.zone_names(), "zone_all": {}, "zone_still": {}, "zone_trem": {}, "zone_h": {},
           "zone_cfg": {zz["name"]: zz for zz in st.zones}}
    for i, name in enumerate(st.zone_names()):
        c = N_HEAD + N_PER_ZONE * i
        out["zone_all"][name] = arr[:, c]
        out["zone_still"][name] = arr[:, c + 1]
        out["zone_trem"][name] = arr[:, c + 2]
        out["zone_h"][name] = mm(arr[:, c + 3])
    return out


# ---------------------------------------------------------------- 머문 구간 찾기

def find_segments(t, n_zone, n_slow, window=2.0, presence=0.5, share=0.75, min_pts=2, min_dur=0.5, n_trem=None, tremor=None,
                  gap=None, below_head=None):
    """'느린 손이 구역에 머문' 구간.

    프레임마다 그 프레임을 가운데 둔 window초 창을 보고, 아래 두 조건을 모두 만족하면 '머묾'
      1) 창 안 프레임의 presence 비율 이상에 구역 점이 min_pts개 이상 있음 (손이 거기 있음. 점이 깜빡여도 됨)
      2) 창 안 구역 점 중 share 비율 이상이 느린 점 (손이 크게 움직이지 않음)
      3) (tremor를 주면) 창 안 구역 점 중 tremor 비율 이상이 떨림 점(도플러 2칸)
         손을 대고 버티면 미세하게 떨고, 걸쳐 둔 팔꿈치·몸은 완전히 멈춰 있는 차이를 쓴다 (앉은 사람 몸과 겹치는 구역용)
      4) (below_head를 주면) 창 안 프레임의 절반 이상에서 머리 높이 − 구역 점 높이(gap) ≥ below_head
         = 구역 점이 그 사람의 꼭대기가 아니라 머리보다 아래에 있는 손. 앉아서 뒷벽에 머리를 기댄 것과 구분 (선반 아래용)
    '머묾'이 min_dur초 이상 이어진 구간을 돌려준다. 반환: [(시작 초, 끝 초, 구간 느린 점 비율), ...]
    (예전 규칙 '정지 점 3개 이상이 1초 연속'은 손 점이 깜빡여서 실제 녹화에서 거의 안 잡혀 바꿈)
    """
    t = np.asarray(t, dtype=float)
    tot = np.asarray(n_zone, dtype=float)
    sl = np.asarray(n_slow, dtype=float)
    if len(t) < 2:
        return []
    pres = (tot >= min_pts).astype(float)
    lo = np.searchsorted(t, t - window / 2)
    hi = np.searchsorted(t, t + window / 2)
    cs = lambda a: np.concatenate([[0.0], np.cumsum(a)])  # noqa: E731
    cp, ct, cl = cs(pres), cs(tot), cs(sl)
    wp = (cp[hi] - cp[lo]) / np.maximum(hi - lo, 1)
    ws = (cl[hi] - cl[lo]) / np.maximum(ct[hi] - ct[lo], 1)
    on = (wp >= presence) & (ws >= share)
    if tremor and n_trem is not None:
        cr = cs(np.asarray(n_trem, dtype=float))
        on &= (cr[hi] - cr[lo]) / np.maximum(ct[hi] - ct[lo], 1) >= tremor
    if below_head is not None and gap is not None:
        g = np.asarray(gap, dtype=float)
        ok = np.isfinite(g)
        cg, cv = cs((ok & (np.nan_to_num(g) >= below_head)).astype(float)), cs(ok.astype(float))
        on &= (cv[hi] - cv[lo] > 0) & ((cg[hi] - cg[lo]) / np.maximum(cv[hi] - cv[lo], 1) >= 0.5)
    out, start = [], None
    for i, v in enumerate(np.append(on, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            a, b = start, i - 1
            if t[b] - t[a] >= min_dur:
                out.append((float(t[a]), float(t[b]), float(sl[a:b + 1].sum() / max(tot[a:b + 1].sum(), 1))))
            start = None
    return out


def segments_by_session(feat, zone, **kw) -> list[tuple[int, float, float, float]]:
    """세션별로 find_segments. 반환: [(세션, 시작, 끝, 느린 점 비율), ...]"""
    res = []
    cfg = feat.get("zone_cfg", {}).get(zone, {})
    tremor = cfg.get("tremor")  # 구역 설정에 "tremor": 0.45 처럼 주면 떨림 조건 추가
    below = cfg.get("below_head")  # "below_head": 0.08 처럼 주면 '머리보다 아래' 조건 추가
    gap = feat["top"] - feat["zone_h"][zone]
    for s in np.unique(feat["session"]):
        m = feat["session"] == s
        for a, b, val in find_segments(feat["t"][m], feat["zone_all"][zone][m], feat["zone_still"][zone][m],
                                       n_trem=feat["zone_trem"][zone][m], tremor=tremor,
                                       gap=gap[m], below_head=below, **kw):
            res.append((int(s), a, b, val))
    return res


# ---------------------------------------------------------------- 높은 곳 (환풍기 등): 변기 등에 올라섬

def _longest(on, t):
    best = cur = 0.0
    dt = float(np.median(np.diff(t))) if len(t) > 1 else 0.0
    for v in on:
        cur = cur + dt if v else 0.0
        best = max(best, cur)
    return best


def climb_scores(feat, rise=0.2, base_sec=4.0, edge=1.9, present=200, vanish=60, smooth=1.0, away=0.9, door_still=3.0,
                 stand_min=1.30, min_height=0.0) -> dict:
    """환풍기처럼 높은 곳에 손을 대려면 변기(시트 약 0.32 m) 등에 올라서야 한다. 두 신호를 본다.

    ① 상승: '자리 잡은' 프레임(사람 있음 + 문이 안 움직임(문 범위 움직이는 점 1초 평균 < door_still개) + 몸 중심이 문에서
       떨어짐(뒷벽에서 away m 이하))만 쓴다. 처음 자리 잡은 뒤 base_sec초 동안의 머리 높이(1초 중앙값) 중 가장 높은 값을
       '서 있는 높이'로 잡고, 자리 잡은 프레임에서 그보다 rise(m) 이상 높은 상태가 이어진 최장 시간.
       (11-04 수정) 서 있는 높이는 stand_min ~ 1.9 m 값만, 처음 그런 값부터 base_sec초 동안의 중앙값.
       최댓값으로 하면 들어올 때 문 위쪽 번짐(1.7 ~ 1.85 m)이 기준이 되고, 높이 제한이 없으면 바로 앉은 사람의 앉은 높이가 기준이 됨
       min_height: 상승으로 셀 머리 높이의 최솟값(m). 서 있는 높이를 낮게 잘못 잰 경우 그냥 일어선 것(1.55 m 안팎)이 상승으로 잡히는 것을 막음
       1.9 m 이상(시야 위쪽 끝 번짐)은 기준 계산에서 뺀다
       (들어오는 중·문 근처에서 재면 문 위쪽 번짐 때문에 서 있는 높이가 1.7 ~ 1.85 m로 잘못 잡힌다. 1102 들어오기·나가기:
        예전 1.45 ~ 1.85 m → 자리 잡은 뒤 1.40 ~ 1.57 m(희선 1.40, kb 1.57)로 일정해짐. 나갈 때 문 근처 번짐 상승도 빠짐)
       (중앙값으로 잡으면 들어오자마자 앉거나 숙인 녹화에서 기준이 낮게 잡혀, 나중에 그냥 일어선 것도 상승으로 셈.
        1102: 중앙값 기준이면 normal_full_kb_3이 9초 상승으로 오경보, 최댓값 기준이면 정상 최대 2.2초)
       (사람마다 키가 달라서 절대 높이 대신 자기 기준 대비 상승을 본다)
    ② 사라짐: 칸 안 점(1초 평균)이 present개 이상이던 사람이 vanish개 미만으로 줄었다가 다시 present개 이상으로 돌아온,
       그 사이 최장 시간. 이 설치(레이더 2.0 m, 50° 숙임)에서는 변기에 올라서면 상체가 레이더 높이 근처로 올라가
       시야 밖으로 나가서 점이 거의 사라진다. 각도를 바꾸면 이 신호는 약해지고 ①이 강해질 수 있다
    반환: {"rise": 초, "vanish": 초, "base": 서 있는 높이(m)}
    """
    out = {"rise": 0.0, "vanish": 0.0, "base": float("nan")}
    for s in np.unique(feat["session"]):
        m = feat["session"] == s
        t, top, n = feat["t"][m], feat["top"][m], feat["n_in"][m].astype(float)
        dm, by = feat["door_move"][m].astype(float), feat["body_y"][m]
        if len(t) < 3:
            continue
        lo = np.searchsorted(t, t - smooth / 2)
        hi = np.searchsorted(t, t + smooth / 2)
        med = lambda x, lo=lo, hi=hi: np.array([np.nanmedian(x[a:b]) if np.isfinite(x[a:b]).any() else np.nan for a, b in zip(lo, hi, strict=True)])  # noqa: E731
        sn = np.array([n[a:b].mean() for a, b in zip(lo, hi, strict=True)])
        sd = np.array([dm[a:b].mean() for a, b in zip(lo, hi, strict=True)])
        sh, sy = med(top), med(by)
        settled = (sn >= present) & (sd < door_still) & np.isfinite(sy) & (sy <= away)
        stand = settled & np.isfinite(sh) & (sh >= stand_min) & (sh < edge)
        here = np.where(stand)[0]
        if len(here):
            t0 = t[here[0]]
            w = stand & (t <= t0 + base_sec)
            if w.sum() >= 20:
                base = float(np.median(sh[w]))
                out["base"] = base
                # 상승 판정 때는 몸 중심을 못 잰 프레임(올라서서 점이 줄어든 때)도 허용. 문 근처·문 움직임만 뺀다
                calm = (sd < door_still) & ~(np.isfinite(sy) & (sy > away))
                out["rise"] = max(out["rise"], _longest(calm & np.isfinite(sh) & (sh >= base + rise) & (sh >= min_height), t))
        low, high = sn < vanish, sn >= present
        i = 0
        while i < len(t):
            if low[i]:
                j = i
                while j < len(t) and low[j]:
                    j += 1
                if high[:i].any() and high[j:].any():
                    out["vanish"] = max(out["vanish"], float(t[j - 1] - t[i]))
                i = j
            else:
                i += 1
    return out


def add_segment_args(ap):
    g = ap.add_argument_group("머문 구간 기준 (느린 손 규칙)")
    g.add_argument("--window", type=float, default=2.0, help="판단 창 길이(초)")
    g.add_argument("--presence", type=float, default=0.5, help="창 안 프레임 중 이 비율 이상에 구역 점이 있어야 '손이 있음'")
    g.add_argument("--share", type=float, default=0.75, help="창 안 구역 점 중 이 비율 이상이 느린 점이어야 '머묾'")
    g.add_argument("--min-pts", type=float, default=2, help="한 프레임에 구역 점이 이 개수 이상이면 '점 있음'")
    g.add_argument("--min-dur", type=float, default=0.5, help="이보다 짧은 머묾은 버림(초)")
    g = ap.add_argument_group("점 분류 기준")
    g.add_argument("--moving", type=float, default=0.4, help="움직이는 점 |doppler| 기준 (m/s, 칸 안 움직임 그래프용)")
    g.add_argument("--static", type=float, default=0.15, help="느린 점 |doppler| 기준 (m/s, 기본 0.15 = 도플러 0 ~ 2칸)")
    ap.add_argument("--zones", default=None, help="다른 구역 파일을 쓸 때 (보통 비움 → stall_1102.json의 구역)")


def seg_kw(a) -> dict:
    return {"window": a.window, "presence": a.presence, "share": a.share, "min_pts": a.min_pts, "min_dur": a.min_dur}
