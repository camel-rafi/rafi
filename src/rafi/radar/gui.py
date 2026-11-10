r"""RaFi 레이더 실시간 탐지 화면 (레이더 PC에서)

  python -m rafi.radar.gui          (또는 저장소의 scripts\radar_gui.bat 더블클릭)

입력칸을 채우고 [실시간 탐지 시작]을 누르면 run.py와 같은 탐지 코드(session.py)가 돈다.
기본값은 실제로 녹화한 칸(우정원 7층, config/radar/stall_1105.json)과 레이더 기본 주소(192.168.30.1).
마지막으로 쓴 입력값은 out/rt/gui_settings.json에 저장되어 다음에 그대로 뜬다.

칸 크기·레이더 위치·각도를 고치면 설정 파일은 그대로 두고 out/rt/stall_active.json에 덮어쓴 사본으로 탐지한다.
비품 구역(디스펜서, 위생함 등)의 위치는 설정 파일 값 그대로다. 다른 칸에 설치하면 칸 설정 파일을 새로 만든다.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

from .session import POSE_KEYS, REPO, Settings, run_session

SETTINGS_FILE = REPO / "out" / "rt" / "gui_settings.json"
DEFAULT_REC = REPO.parent / "sg-radar-tools" / "recordings" / "record_1105"

# 글씨체: 저장소에 넣어 둔 Pretendard(fonts 폴더, OFL 1.1)를 먼저, 없으면 Windows 기본(맑은 고딕)
FONT_DIR = Path(__file__).resolve().parent / "fonts"
FONT_ORDER = ("Pretendard", "Malgun Gothic", "맑은 고딕", "Noto Sans CJK KR", "NanumGothic")
FONT_CHOICE = os.environ.get("RAFI_FONT", "")  # 다른 글씨체로 보고 싶을 때 (예: set RAFI_FONT=Malgun Gothic)


def load_bundled_fonts():
    """fonts 폴더의 글씨체를 이 프로그램에서만 쓰도록 등록 (Windows). 설치하지 않아도 된다."""
    try:
        from ctypes import windll
    except ImportError:
        return
    for f in FONT_DIR.glob("*.[ot]tf"):
        windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)  # 0x10 = FR_PRIVATE (이 프로그램만)


# 화면 글자는 영어. 서버로 보내는 메시지(근거 문장 포함)는 그대로 두고 화면에 보일 때만 옮긴다.
ZONE_EN = {
    "dispenser_low": "Dispenser (front/below)", "dispenser_top": "Dispenser (top)",
    "sanbox_low": "Sanitary bin (front/below)", "sanbox_top": "Sanitary bin (top)",
    "under_shelf": "Under shelf", "stall": "Whole stall", "high": "High area",
    "wall_plain": "Wall without dispenser", "door_hand": "Door",
}
ZONE_KO_EN = {"디스펜서 앞·아래": "Dispenser (front/below)", "디스펜서 위쪽": "Dispenser (top)",
              "디스펜서 없는 벽": "Wall without dispenser", "문": "Door",
              "위생함 앞·아래": "Sanitary bin (front/below)", "위생함 위쪽": "Sanitary bin (top)",
              "선반 아래": "Under shelf", "칸 전체": "Whole stall", "높은 곳": "High area"}

_REASON_EN = [  # 탐지기(detector.py)가 만드는 근거 문장 → 영어
    (r"^(.+?) 손 머묾 ([\d.]+)초(?: \((.+)\))?$",
     lambda m: f"Hand held at {ZONE_KO_EN.get(m[1], m[1])} for {m[2]} s"
     + (f" ({m[3].replace('손 떨림', 'hand tremor').replace('머리보다 아래', 'below head')})" if m[3] else "")),
    (r"^머리 숙임 ([\d.]+)초 \(서 있던 높이 ([\d.]+) m → ([\d.]+) m, 머리 위치 왼쪽 벽에서 ([\d.]+) m · 뒷벽에서 ([\d.]+) m\)$",
     lambda m: f"Head lowered for {m[1]} s (standing height {m[2]} m → {m[3]} m, head {m[4]} m from left wall, "
     f"{m[5]} m from back wall)"),
    (r"^선반 아래 선 머리 ([\d.]+)초 \(머리 ([\d.]+) m, 뒷벽에서 ([\d.]+) m\)$",
     lambda m: f"Standing head under shelf for {m[1]} s (head {m[2]} m, {m[3]} m from back wall)"),
    (r"^서 있던 높이\(([\d.]+) m\)보다 ([\d.]+) m 높은 상태 ([\d.]+)초 \(올라섬\)$",
     lambda m: f"{m[2]} m above standing height ({m[1]} m) for {m[3]} s (climbed up)"),
    (r"^문이 안 열렸는데 사람 점이 사라진 상태 ([\d.]+)초 \(올라서서 시야 위로 나감\)$",
     lambda m: f"Person points vanished with door closed for {m[1]} s (climbed out of view)"),
]


def reason_en(text: str) -> str:
    for pat, fn in _REASON_EN:
        m = re.match(pat, text)
        if m:
            return fn(m)
    return text


def zone_en(roi: str) -> str:
    return ZONE_EN.get(roi, roi)


_LOG_EN = [  # session.py 진행 안내 → 영어
    (r"^(.+)에 연결하는 중$", lambda m: f"Connecting to {m[1]}"),
    (r"^배경 칸 (\d+)개 \(빈 칸 녹화에서\)$", lambda m: f"Background: {m[1]} cells (from empty-stall recording)"),
    (r"^주의: .*배경 없이 탐지함$", lambda m: "Warning: no empty-stall recording found in that folder; running without background"),
    (r"^배경 배우는 중: (\d+)초 동안 칸을 비워 두세요$", lambda m: f"Learning background: keep the stall empty for {m[1]} s"),
    (r"^배경 칸 (\d+)개 배움$", lambda m: f"Background learned: {m[1]} cells"),
    (r"^탐지 시작$", lambda m: "Detection started"),
    (r"^레이더 연결 끊김: (.*)$", lambda m: f"Radar disconnected: {m[1].replace('레이더가 연결을 끊음', 'radar closed the connection')}"),
    (r"^레이더 다시 연결됨$", lambda m: "Radar reconnected"),
    (r"^멈춤$", lambda m: "Stopped"),
    (r"^재생 끝$", lambda m: "Playback finished"),
    (r"^(.*)녹화 파일이 없음: (.*)$", lambda m: f"{m[1]}Recording file not found: {m[2]}"),
    (r"^(.*)빈 칸 녹화 폴더가 없음: (.*)$", lambda m: f"{m[1]}Empty-stall recording folder not found: {m[2]}"),
]


def log_en(text: str) -> str:
    for pat, fn in _LOG_EN:
        m = re.match(pat, text)
        if m:
            return fn(m)
    return text

# 화면 색 (Windows 10 설정 창 느낌: 왼쪽 연회색 메뉴, 오른쪽 흰 바탕, 파란 글자 링크, 회색 버튼)
SIDE, MAIN, LINE = "#f2f2f2", "#ffffff", "#d9d9d9"
TEXT, HINT, LINK, ACCENT = "#000000", "#5d5d5d", "#0067b8", "#0078d4"
OKC, WARNC, ERRC = "#107c10", "#9d5d00", "#c42b1c"
# 상태 표시: (아이콘 색, 아이콘 글자, 제목 색)
C = {
    "idle": ("#8a8a8a", "–", TEXT), "busy": (ACCENT, "…", TEXT), "ok": (OKC, "✓", TEXT),
    "alert": (ERRC, "!", ERRC), "warn": (WARNC, "!", TEXT), "err": (ERRC, "×", ERRC),
}

# (설정 키, 화면 이름, 칸 설정 파일에서 꺼내는 방법)
POSE_FIELDS = [
    ("width", "Stall width (left wall → right partition)", "m"),
    ("depth", "Stall depth (back wall → door)", "m"),
    ("height", "Stall height (floor → ceiling)", "m"),
    ("pos_x", "Radar X (from left wall)", "m"),
    ("pos_y", "Radar Y (from back wall)", "m"),
    ("pos_z", "Radar height (from floor)", "m"),
    ("pitch_deg", "Tilt down (level 0°, straight down 90°)", "°"),
    ("yaw_deg", "Facing direction (toward door = 90°)", "°"),
    ("roll_deg", "Roll (upside down = 180°)", "°"),
]


def read_pose(stall_path: str) -> dict:
    cfg = json.loads(Path(stall_path).read_text(encoding="utf-8"))
    x, y, z = cfg["radar_pos"]
    return {"width": cfg["width"], "depth": cfg["depth"], "height": cfg["height"], "pos_x": x, "pos_y": y,
            "pos_z": z, "pitch_deg": cfg.get("pitch_deg", 0.0), "yaw_deg": cfg.get("yaw_deg", 90.0),
            "roll_deg": cfg.get("roll_deg", 0.0), "mirror_x": bool(cfg.get("mirror_x", False))}


def to_overrides(pose: dict, base: dict) -> dict:
    """화면 값 중 설정 파일과 다른 것만 칸 설정 키로."""
    out = {}
    for k in ("width", "depth", "height", "pitch_deg", "yaw_deg", "roll_deg"):
        if abs(pose[k] - base[k]) > 1e-9:
            out[k] = pose[k]
    pos, bpos = [pose["pos_x"], pose["pos_y"], pose["pos_z"]], [base["pos_x"], base["pos_y"], base["pos_z"]]
    if any(abs(a - b) > 1e-9 for a, b in zip(pos, bpos, strict=True)):
        out["radar_pos"] = pos
    if pose["mirror_x"] != base["mirror_x"]:
        out["mirror_x"] = pose["mirror_x"]
    assert set(out) <= set(POSE_KEYS)
    return out


def defaults() -> dict:
    s = Settings()
    d = asdict(s)
    d["bg_folder"] = str(DEFAULT_REC)
    d["jsonl_on"] = False
    d["pose"] = None  # None = 설정 파일 값
    return d


def load_saved() -> dict:
    d = defaults()
    try:
        saved = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        d.update({k: v for k, v in saved.items() if k in d})
    except (OSError, ValueError):
        pass
    return d


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("RaFi Radar Live Detection")
        # 고해상도 화면(배율 125%, 150% 등): 픽셀로 정한 크기도 같이 키운다
        self.k = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        root.geometry(f"{int(1180 * self.k)}x{int(760 * self.k)}")
        root.minsize(int(980 * self.k), int(640 * self.k))
        self._fonts()
        self.q: queue.Queue = queue.Queue()
        self.stop = threading.Event()
        self.worker: threading.Thread | None = None
        self.open_events: dict[str, dict] = {}
        self.n_alarms = 0
        self.learn_left = 0.0
        self.disconnected = False
        self.base_pose: dict = {}

        d = load_saved()
        self.v = {
            "source": tk.StringVar(value=d["source"]), "host": tk.StringVar(value=d["host"]),
            "port": tk.StringVar(value=str(d["port"])), "replay": tk.StringVar(value=d["replay"]),
            "speed": tk.StringVar(value=str(d["speed"])), "stall": tk.StringVar(value=d["stall"]),
            "bg_mode": tk.StringVar(value=d["bg_mode"]), "learn_s": tk.StringVar(value=str(d["learn_s"])),
            "bg_folder": tk.StringVar(value=d["bg_folder"]), "post": tk.StringVar(value=d["post"]),
            "device_id": tk.StringVar(value=d["device_id"]), "room": tk.StringVar(value=d["room"]),
            "jsonl_on": tk.BooleanVar(value=d["jsonl_on"]),
            "restroom": tk.StringVar(value=d["restroom"] or "female"),
        }
        self.pv = {k: tk.StringVar() for k, _, _ in POSE_FIELDS}
        self.mirror = tk.BooleanVar()

        self._layout()
        self._load_pose(d["pose"])
        self._source_changed(first=True)
        # 왼쪽 칸 폭을 내용에 맞춤 (글자 크기·화면 배율과 상관없이 입력칸·버튼이 잘리지 않게)
        root.update_idletasks()
        self.left_canvas.configure(width=self.left_frame.winfo_reqwidth())
        self._set_banner("idle", "Ready", "Check the settings, then select Start live detection.")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(100, self._poll)

    # ------------------------------------------------------------ 모양
    def _fonts(self):
        fams = set(font.families())
        want = [FONT_CHOICE] if FONT_CHOICE else []
        fam = next((f for f in (*want, *FONT_ORDER) if f in fams), None)
        self.fam = fam or "TkDefaultFont"
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont", "TkFixedFont"):
            f = font.nametofont(name)
            if fam:
                f.configure(family=fam)
            f.configure(size=10)
        F = fam or "TkDefaultFont"
        self.f_big = font.Font(family=F, size=20)
        self.f_mid = font.Font(family=F, size=11)
        self.f_bold = font.Font(family=F, size=10, weight="bold")
        self.f_head = font.Font(family=F, size=13)
        self.f_page = font.Font(family=F, size=17)
        self.root.configure(bg=SIDE)
        st = ttk.Style()
        st.theme_use("clam")  # 색을 마음대로 정할 수 있는 테마 (vista 테마는 바탕색을 무시함)
        st.configure(".", background=SIDE, foreground=TEXT, bordercolor=LINE, focuscolor=SIDE,
                     troughcolor=SIDE, lightcolor=SIDE, darkcolor=SIDE)
        for w in ("TFrame", "TLabel", "TRadiobutton", "TCheckbutton"):
            st.configure(w, background=SIDE)
            st.configure("Main." + w, background=MAIN)
        st.map("TRadiobutton", background=[("active", SIDE)], foreground=[("disabled", "#8a8a8a")])
        st.map("TCheckbutton", background=[("active", SIDE)], foreground=[("disabled", "#8a8a8a")])
        st.configure("TRadiobutton", indicatorbackground=MAIN, indicatorforeground=TEXT)
        st.configure("TCheckbutton", indicatorbackground=MAIN, indicatorforeground=TEXT)
        st.map("TLabel", foreground=[("disabled", "#8a8a8a")])
        st.configure("Hint.TLabel", foreground=HINT)
        st.configure("Main.Hint.TLabel", background=MAIN, foreground=HINT)
        st.configure("Head.TLabel", font=self.f_head)
        st.configure("Page.TLabel", font=self.f_page)
        st.configure("Main.Head.TLabel", background=MAIN, font=self.f_head)
        st.configure("Link.TLabel", foreground=LINK)
        st.map("Link.TLabel", foreground=[("disabled", "#8a8a8a"), ("active", "#004e8c")])
        # 입력칸: 흰 바탕, 회색 테두리, 누르면 파란 테두리
        st.configure("TEntry", fieldbackground=MAIN, bordercolor="#8a8a8a", lightcolor=MAIN, darkcolor=MAIN,
                     padding=(4, 3))
        st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)],
               fieldbackground=[("disabled", "#ebebeb")], foreground=[("disabled", "#7a7a7a")])
        # 버튼: Windows 설정의 회색 네모 버튼
        st.configure("TButton", background="#cccccc", foreground=TEXT, bordercolor="#cccccc", lightcolor="#cccccc",
                     darkcolor="#cccccc", relief="flat", padding=(12, 4))
        st.map("TButton", background=[("disabled", "#e5e5e5"), ("pressed", "#999999"), ("active", "#b3b3b3")],
               bordercolor=[("active", "#7a7a7a")], foreground=[("disabled", "#8a8a8a")],
               lightcolor=[("active", "#b3b3b3")], darkcolor=[("active", "#b3b3b3")])
        st.configure("Start.TButton", font=self.f_bold, padding=(12, 9), background=ACCENT, foreground="#ffffff",
                     bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT)
        st.map("Start.TButton", background=[("disabled", "#cccccc"), ("pressed", "#005a9e"), ("active", "#106ebe")],
               foreground=[("disabled", "#8a8a8a")], lightcolor=[("active", "#106ebe")],
               darkcolor=[("active", "#106ebe")], bordercolor=[("active", "#106ebe")])
        st.configure("Stop.TButton", font=self.f_bold, padding=(12, 9))
        # 표
        st.configure("Treeview", background=MAIN, fieldbackground=MAIN, rowheight=font.nametofont("TkDefaultFont").metrics("linespace") + int(10 * self.k),
                     bordercolor=LINE,
                     lightcolor=MAIN, darkcolor=MAIN)
        st.map("Treeview", background=[("selected", "#cce4f7")], foreground=[("selected", TEXT)])
        st.configure("Treeview.Heading", background=MAIN, foreground=HINT, relief="flat", bordercolor=LINE,
                     lightcolor=MAIN, darkcolor=MAIN, padding=(4, 4))
        st.map("Treeview.Heading", background=[("active", "#f2f2f2")])
        st.configure("Vertical.TScrollbar", background="#cdcdcd", troughcolor=MAIN, bordercolor=MAIN,
                     arrowcolor="#606060", lightcolor="#cdcdcd", darkcolor="#cdcdcd")
        st.configure("Side.Vertical.TScrollbar", background="#cdcdcd", troughcolor=SIDE, bordercolor=SIDE,
                     arrowcolor="#606060", lightcolor="#cdcdcd", darkcolor="#cdcdcd")

    def _layout(self):
        # 왼쪽: 입력 (연회색, 스크롤)
        left_outer = ttk.Frame(self.root)
        left_outer.pack(side="left", fill="y")
        ttk.Label(left_outer, text="Radar live detection", style="Page.TLabel").pack(anchor="w", padx=20, pady=(16, 6))
        b = ttk.Frame(left_outer)
        b.pack(side="bottom", fill="x", padx=20, pady=(10, 16))
        self.start_b = ttk.Button(b, text="Start live detection", style="Start.TButton", command=self._start)
        self.start_b.pack(side="left", fill="x", expand=True)
        self.stop_b = ttk.Button(b, text="Stop", style="Stop.TButton", command=self._stop, state="disabled")
        self.stop_b.pack(side="left", padx=(8, 0))
        tk.Frame(left_outer, height=1, bg=LINE).pack(side="bottom", fill="x", padx=20)
        canvas = tk.Canvas(left_outer, highlightthickness=0, width=450, bg=SIDE)
        self.left_canvas = canvas
        sb = ttk.Scrollbar(left_outer, orient="vertical", command=canvas.yview, style="Side.Vertical.TScrollbar")
        left = ttk.Frame(canvas)
        self.left_frame = left
        left.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=left, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(20, 0))
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.inputs: list[tk.Widget] = []

        # 1. 입력
        f = self._section(left, "Radar input")
        for val, txt in (("radar", "Radar (Wi-Fi)"), ("emulator", "Emulator (this PC, 127.0.0.1)"),
                         ("replay", "Play a recording file")):
            self._track(ttk.Radiobutton(f, text=txt, value=val, variable=self.v["source"],
                                        command=self._source_changed)).pack(anchor="w")
        g = ttk.Frame(f)
        g.pack(fill="x", pady=(6, 0))
        self.host_e = self._row(g, 0, "Radar address", self.v["host"])
        self._row(g, 1, "Port", self.v["port"], width=8)
        self.replay_e = self._row(g, 2, "Recording", self.v["replay"],
                                  browse=lambda: self._pick_file(self.v["replay"], "*.pcr"))
        self.speed_e = self._row(g, 3, "Playback speed (1 = real time)", self.v["speed"], width=8)

        # 2. 칸
        f = self._section(left, "Stall and radar installation")
        g = ttk.Frame(f)
        g.pack(fill="x")
        self._row(g, 0, "Stall config", self.v["stall"], browse=self._pick_stall)
        ttk.Label(f, text="Defaults are the recorded stall (Woojungwon 7F, Nov 5 installation). If the installation changes, edit only the values below.",
                  style="Hint.TLabel", wraplength=int(400 * self.k)).pack(anchor="w", pady=(2, 8))
        g = ttk.Frame(f)
        g.pack(fill="x")
        for i, (k, label, unit) in enumerate(POSE_FIELDS):
            ttk.Label(g, text=label).grid(row=i, column=0, sticky="w", pady=1)
            e = self._track(ttk.Entry(g, textvariable=self.pv[k], width=8, justify="right"))
            e.grid(row=i, column=1, sticky="e", padx=(6, 2))
            ttk.Label(g, text=unit, width=2).grid(row=i, column=2, sticky="w")
            self.pv[k].trace_add("write", lambda *_: self._pose_dirty())
        g.columnconfigure(0, weight=1)
        self._track(ttk.Checkbutton(f, text="Mirror left/right (on when the radar faces the door)", variable=self.mirror,
                                    command=self._pose_dirty)).pack(anchor="w", pady=(4, 0))
        r = ttk.Frame(f)
        r.pack(fill="x", pady=(6, 0))
        ttk.Label(r, text="Restroom").pack(side="left")
        for val, txt in (("female", "Women's"), ("male", "Men's")):
            self._track(ttk.Radiobutton(r, text=txt, value=val, variable=self.v["restroom"],
                                        command=self._pose_dirty)).pack(side="left", padx=(10, 0))
        ttk.Label(f, text="Sets the expected seated head height (seat 0.30–0.35 m + average Korean sitting height).",
                  style="Hint.TLabel", wraplength=int(400 * self.k)).pack(anchor="w")
        h = ttk.Frame(f)
        h.pack(fill="x", pady=(4, 0))
        self._track(self._link(h, "Reset to file values", lambda: self._load_pose(None))).pack(side="left")
        self.pose_note = ttk.Label(h, text="", foreground=WARNC)
        self.pose_note.pack(side="left", padx=(12, 0))

        # 3. 배경
        f = self._section(left, "Background (removes wall and fixture reflections)")
        r1 = ttk.Frame(f)
        r1.pack(fill="x")
        self._track(ttk.Radiobutton(r1, text="Learn for", value="learn", variable=self.v["bg_mode"])).pack(side="left")
        self._track(ttk.Entry(r1, textvariable=self.v["learn_s"], width=5, justify="right")).pack(side="left", padx=4)
        ttk.Label(r1, text="s after start, stall empty (new installation)").pack(side="left")
        self._track(ttk.Radiobutton(f, text="Use empty-stall recordings (same installation as recorded)", value="folder",
                                    variable=self.v["bg_mode"])).pack(anchor="w", pady=(4, 0))
        g = ttk.Frame(f)
        g.pack(fill="x")
        self._row(g, 0, "Folder", self.v["bg_folder"], browse=lambda: self._pick_dir(self.v["bg_folder"]))

        # 4. 서버
        f = self._section(left, "Send to server")
        g = ttk.Frame(f)
        g.pack(fill="x")
        self._row(g, 0, "Server URL", self.v["post"])
        ttk.Label(f, text="e.g. http://10.1.101.239:8000/api/v1/messages  (leave empty to not send)",
                  style="Hint.TLabel").pack(anchor="w")
        h = ttk.Frame(f)
        h.pack(fill="x", pady=(4, 0))
        self._track(self._link(h, "Test server connection", self._check_server)).pack(side="left")
        self.server_note = ttk.Label(h, text="", wraplength=280)
        self.server_note.pack(side="left", padx=8)
        g = ttk.Frame(f)
        g.pack(fill="x", pady=(6, 0))
        self._row(g, 0, "Device ID", self.v["device_id"], width=12)
        self._row(g, 1, "Room ID", self.v["room"], width=12)
        self._track(ttk.Checkbutton(f, text="Also save sent messages to out/rt/messages.jsonl",
                                    variable=self.v["jsonl_on"])).pack(anchor="w", pady=(4, 0))

        # 오른쪽: 상태 (흰 바탕)
        right = ttk.Frame(self.root, style="Main.TFrame", padding=(28, 16, 24, 16))
        right.pack(side="left", fill="both", expand=True)
        self.banner = tk.Frame(right, bd=0, bg=MAIN)
        self.banner.pack(fill="x")
        n = int(46 * self.k)
        self.b_icon = tk.Canvas(self.banner, width=n, height=n, bg=MAIN, highlightthickness=0)
        self.b_icon.pack(side="left", anchor="n", pady=(4, 0))
        txt = tk.Frame(self.banner, bg=MAIN)
        txt.pack(side="left", fill="x", expand=True, padx=(14, 0))
        self.b_title = tk.Label(txt, font=self.f_big, anchor="w", bg=MAIN)
        self.b_title.pack(fill="x")
        self.b_sub = tk.Label(txt, font=self.f_mid, anchor="w", justify="left", wraplength=640, bg=MAIN, fg=HINT)
        self.b_sub.pack(fill="x", pady=(2, 0))
        txt.bind("<Configure>", lambda e: self.b_sub.configure(wraplength=max(e.width - 10, 200)))
        tk.Frame(right, height=1, bg=LINE).pack(fill="x", pady=(16, 0))

        st = ttk.Frame(right, style="Main.TFrame")
        st.pack(fill="x", pady=(10, 6))
        self.s = {}
        for i, (k, t) in enumerate((("radar", "Radar"), ("fps", "Frames/s"), ("person", "Person in stall"),
                                    ("server", "Server"), ("alarms", "Alerts"))):
            cell = ttk.Frame(st, style="Main.TFrame", padding=(0, 4, 10, 4))
            cell.grid(row=0, column=i, sticky="nsew")
            st.columnconfigure(i, weight=1)
            ttk.Label(cell, text=t, style="Main.Hint.TLabel").pack(anchor="w")
            self.s[k] = ttk.Label(cell, text="—", style="Main.TLabel", font=self.f_mid)
            self.s[k].pack(anchor="w")
        tk.Frame(right, height=1, bg=LINE).pack(fill="x", pady=(4, 0))

        ttk.Label(right, text="Alert history", style="Main.Head.TLabel").pack(anchor="w", pady=(14, 6))
        cols = ("time", "state", "roi", "score", "reason")
        tv_frame = ttk.Frame(right, style="Main.TFrame")
        tv_frame.pack(fill="both", expand=True)
        self.tv = ttk.Treeview(tv_frame, columns=cols, show="headings", height=8)
        fm = font.nametofont("TkDefaultFont")
        pad = int(24 * self.k)
        for c, t, sample, stretch in (("time", "Time", "00:00:00", False), ("state", "Status", "Active", False),
                                      ("roi", "Zone", "Sanitary bin (front/below)", False),
                                      ("score", "Score", "Score", False), ("reason", "Reason", "x" * 40, True)):
            self.tv.heading(c, text=t, anchor="w")
            self.tv.column(c, width=max(fm.measure(sample), fm.measure(t)) + pad, stretch=stretch, anchor="w")
        self.tv.tag_configure("on", background="#fde7e9", foreground=ERRC)
        tsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=tsb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.detail = ttk.Label(right, text="Select an alert to see the full reason here.", style="Main.Hint.TLabel",
                                justify="left", wraplength=640)
        self.detail.pack(fill="x", pady=(4, 0))
        right.bind("<Configure>", lambda e: self.detail.configure(wraplength=max(e.width - 20, 200)))
        self.tv.bind("<<TreeviewSelect>>", self._show_detail)

        ttk.Label(right, text="Activity log", style="Main.Head.TLabel").pack(anchor="w", pady=(14, 6))
        self.log = tk.Text(right, height=7, wrap="word", relief="flat", background=MAIN, padx=8, pady=6,
                           font=(self.fam, 10), highlightthickness=1, highlightbackground=LINE,
                           highlightcolor=LINE, foreground="#333333")
        self.log.pack(fill="x")
        self.log.configure(state="disabled")

    def _show_detail(self, _=None):
        sel = self.tv.selection()
        if sel:
            t, state, roi, score, reason = self.tv.item(sel[0], "values")
            self.detail.configure(text=f"{t}  {roi}  score {score}  ({state})\n" + reason.replace(" / ", "\n"),
                                  foreground=TEXT)

    def _section(self, parent, title):
        if parent.winfo_children():
            tk.Frame(parent, height=1, bg=LINE).pack(fill="x", pady=(14, 0), padx=(0, 16))
        ttk.Label(parent, text=title, style="Head.TLabel").pack(anchor="w", pady=(12, 6))
        f = ttk.Frame(parent, padding=(0, 0, 16, 0))
        f.pack(fill="x")
        return f

    def _link(self, parent, text, command):
        """파란 글자 링크 (Windows 설정의 '선택적 업데이트 보기' 같은 것)."""
        w = ttk.Label(parent, text=text, style="Link.TLabel", cursor="hand2")
        w.bind("<Button-1>", lambda e: command() if not w.instate(["disabled"]) else None)
        w.bind("<Enter>", lambda e: w.state(["active"]) if not w.instate(["disabled"]) else None)
        w.bind("<Leave>", lambda e: w.state(["!active"]))
        return w

    def _track(self, w):
        self.inputs.append(w)
        return w

    def _row(self, g, r, label, var, width=None, browse=None):
        ttk.Label(g, text=label).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 6))
        e = self._track(ttk.Entry(g, textvariable=var, width=width or 24))
        e.grid(row=r, column=1, sticky="ew" if not width else "w", pady=2)
        if browse:
            self._track(ttk.Button(g, text="Browse", command=browse)).grid(row=r, column=2, padx=(4, 0))
        g.columnconfigure(1, weight=1)
        return e

    # ------------------------------------------------------------ 입력
    def _source_changed(self, first=False):
        src = self.v["source"].get()
        self.host_e.configure(state="normal" if src == "radar" else "disabled")
        rep = "normal" if src == "replay" else "disabled"
        self.replay_e.configure(state=rep)
        self.speed_e.configure(state=rep)
        if not first:  # 녹화를 틀 때는 녹화한 설치의 빈 칸 녹화가, 실제 레이더는 새로 배우기가 맞음
            self.v["bg_mode"].set("learn" if src == "radar" else "folder")

    def _pick_file(self, var, pattern):
        p = filedialog.askopenfilename(filetypes=[("Recording", pattern), ("All files", "*.*")],
                                       initialdir=str(Path(var.get()).parent) if var.get() else str(DEFAULT_REC))
        if p:
            var.set(p)

    def _pick_dir(self, var):
        p = filedialog.askdirectory(initialdir=var.get() or str(DEFAULT_REC))
        if p:
            var.set(p)

    def _pick_stall(self):
        p = filedialog.askopenfilename(filetypes=[("Stall config", "*.json")],
                                       initialdir=str(REPO / "config" / "radar"))
        if p:
            self.v["stall"].set(p)
            self._load_pose(None)

    def _load_pose(self, saved: dict | None):
        p = self._stall_path()
        try:  # 저장소 안이면 짧게 (config/radar/stall_1105.json)
            self.v["stall"].set(str(p.resolve().relative_to(REPO.resolve())))
        except ValueError:
            pass
        try:
            self.base_pose = read_pose(str(self._stall_path()))
        except (OSError, ValueError, KeyError) as e:
            self.base_pose = {}
            self.pose_note.configure(text=f"Could not read config file: {e}")
            return
        pose = {**self.base_pose, **(saved or {})}
        for k, _, _ in POSE_FIELDS:
            self.pv[k].set(f"{pose[k]:g}")
        self.mirror.set(pose["mirror_x"])
        if saved is None:  # 파일 값으로 되돌리기 / 파일을 바꿈 → 화장실 종류도 파일 값으로
            try:
                self.v["restroom"].set(json.loads(p.read_text(encoding="utf-8")).get("restroom", "female"))
            except (OSError, ValueError):
                pass
        self._pose_dirty()

    def _stall_path(self) -> Path:
        p = Path(self.v["stall"].get().strip())
        return p if p.is_absolute() else REPO / p

    def _read_pose(self) -> dict:
        p = {}
        for k, label, _ in POSE_FIELDS:
            try:
                p[k] = float(self.pv[k].get())
            except ValueError as e:
                raise ValueError(f"'{label.split(' (')[0]}' is not a number: {self.pv[k].get()!r}") from e
        p["mirror_x"] = bool(self.mirror.get())
        return p

    def _pose_dirty(self):
        if not self.base_pose:
            return
        try:
            ov = to_overrides(self._read_pose(), self.base_pose)
        except ValueError:
            self.pose_note.configure(text="Check the numbers")
            return
        try:
            file_restroom = json.loads(self._stall_path().read_text(encoding="utf-8")).get("restroom", "female")
        except (OSError, ValueError):
            file_restroom = self.v["restroom"].get()
        differs = bool(ov) or self.v["restroom"].get() != file_restroom
        self.pose_note.configure(text="Differs from file (a copy is used)" if differs else "")

    def _collect(self) -> tuple[Settings, dict]:
        v = {k: x.get() for k, x in self.v.items()}
        pose = self._read_pose()
        s = Settings(source=v["source"], host=v["host"].strip(), port=int(v["port"]), replay=v["replay"].strip(),
                     speed=float(v["speed"]), stall=str(self._stall_path()), overrides=to_overrides(pose, self.base_pose),
                     restroom=v["restroom"],
                     bg_mode=v["bg_mode"], learn_s=float(v["learn_s"]), bg_folder=v["bg_folder"].strip(),
                     room=v["room"].strip() or "stall-1", device_id=v["device_id"].strip() or "radar-1",
                     post=v["post"].strip(), jsonl=str(REPO / "out" / "rt" / "messages.jsonl") if v["jsonl_on"] else "")
        if s.source == "radar" and not s.host:
            raise ValueError("Enter the radar address (default 192.168.30.1)")
        if s.source == "replay" and not Path(s.replay).is_file():
            raise ValueError("Choose a recording file")
        if not Path(s.stall).is_file():
            raise ValueError(f"Stall config not found: {s.stall}")
        if s.bg_mode == "folder" and not Path(s.bg_folder).is_dir():
            raise ValueError(f"Empty-stall recording folder not found: {s.bg_folder}")
        if s.bg_mode == "learn" and s.learn_s <= 0:
            raise ValueError("Background learning time must be greater than 0")
        if s.post and not s.post.startswith(("http://", "https://")):
            raise ValueError("Server URL must start with http://")
        save = {**{k: getattr(s, k) for k in ("source", "host", "port", "replay", "speed", "stall", "bg_mode",
                                              "learn_s", "bg_folder", "room", "device_id", "post", "restroom")},
                "stall": v["stall"].strip(), "jsonl_on": bool(v["jsonl_on"]), "pose": pose if s.overrides else None}
        return s, save

    # ------------------------------------------------------------ 서버 확인
    def _check_server(self):
        url = self.v["post"].get().strip()
        if not url:
            self.server_note.configure(text="Enter the server URL first", foreground=ERRC)
            return
        u = urllib.parse.urlsplit(url)
        probe = urllib.parse.urlunsplit((u.scheme, u.netloc, "/api/v1/devices", "", ""))
        self.server_note.configure(text="Checking…", foreground=HINT)

        def work():
            try:
                with urllib.request.urlopen(probe, timeout=3) as r:
                    devs = json.loads(r.read().decode("utf-8"))
                names = ", ".join(d.get("device_id", "?") for d in devs) if isinstance(devs, list) else ""
                self.q.put(("server_check", (True, f"Connected (registered devices: {names or 'none'})")))
            except urllib.error.HTTPError as e:
                self.q.put(("server_check", (e.code < 500, f"Server replied {e.code}: the server is running")))
            except Exception as e:  # noqa: BLE001
                self.q.put(("server_check", (False, f"Can't connect: {getattr(e, 'reason', e)}")))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ 시작·멈춤
    def _start(self):
        try:
            s, save = self._collect()
        except (ValueError, OSError) as e:
            messagebox.showerror("Check settings", str(e))
            return
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(save, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stop.clear()
        self.open_events.clear()
        self.disconnected = False
        self.n_alarms = 0
        self.tv.delete(*self.tv.get_children())
        for k in self.s:
            self.s[k].configure(text="—", foreground="")
        self.s["alarms"].configure(text="0")
        self.s["server"].configure(text="Not sending" if not s.post else "—")
        self._enable_inputs(False)
        self._set_banner("busy", "Starting", "Checking the radar connection and settings.")
        self._log(f"Start: input {dict(radar='radar', emulator='emulator', replay='recording')[s.source]}, "
                  f"stall config {Path(s.stall).name}" + (f" (edited: {', '.join(s.overrides)})" if s.overrides else ""))
        self.worker = threading.Thread(target=run_session, args=(s, self.stop, lambda k, d: self.q.put((k, d))),
                                       daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop.set()
        self.stop_b.configure(state="disabled")
        self._set_banner("busy", "Stopping", "Closing open alerts and sending the last messages to the server.")

    def _enable_inputs(self, on: bool):
        for w in self.inputs:
            try:
                w.configure(state="normal" if on else "disabled")
            except tk.TclError:
                pass
        if on:
            self._source_changed(first=True)
        self.start_b.configure(state="normal" if on else "disabled")
        self.stop_b.configure(state="disabled" if on else "normal")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Close", "Detection is running. Stop and close?"):
                return
            self.stop.set()
            self.worker.join(6)
        self.root.destroy()

    # ------------------------------------------------------------ 상태 표시
    def _set_banner(self, kind, title, sub=""):
        color, glyph, fg = C[kind]
        c = self.b_icon
        c.delete("all")
        n = int(46 * self.k)
        c.create_oval(3, 3, n - 3, n - 3, fill=color, outline=color)
        c.create_text(n // 2, n // 2 - 1, text=glyph, fill="#ffffff", font=(self.fam, 18, "bold"))
        self.b_title.configure(text=title, fg=fg)
        self.b_sub.configure(text=sub)

    def _log(self, text):
        from datetime import datetime
        self.log.configure(state="normal")
        self.log.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _watching(self):
        if self.disconnected:
            self._set_banner("warn", "Radar disconnected",
                             "Check the radar power and the Wi-Fi connection to the radar. Detection resumes "
                             "automatically when it reconnects.")
        elif self.open_events:
            ev = max(self.open_events.values(), key=lambda p: p["score"])
            self._set_banner("alert", f"Suspicious activity: {zone_en(ev['roi'])}",
                             "\n".join(reason_en(r) for r in ev["reasons"]))
        else:
            self._set_banner("ok", "Monitoring: no issues", "Suspected camera installation will show here immediately.")

    def _learn_tick(self):
        if self.learn_left <= 0 or self.stop.is_set():
            return
        self._set_banner("busy", f"Learning background: {self.learn_left:.0f} s",
                         "Keep the stall empty. The radar is learning wall and fixture reflections to tell them "
                         "apart from people.")
        self.learn_left -= 1
        self.root.after(1000, self._learn_tick)

    def _on_msg(self, m):
        p, t = m["payload"], m["type"]
        if t == "radar.occupancy":
            here = p.get("person_count", 0) > 0
            txt = "Yes" + (" (not visible)" if p.get("hidden") else "") if here else "No"
            self.s["person"].configure(text=txt)
        elif t == "radar.detection":
            eid = p.get("event_id", "")
            ongoing = bool(p.get("ongoing"))
            new = eid not in self.open_events
            if ongoing:
                self.open_events[eid] = p
            else:
                self.open_events.pop(eid, None)
            if new and ongoing:
                self._log(f"Alert: {zone_en(p['roi'])}: {reason_en(p['reasons'][0])}")
                self.n_alarms += 1
                self.s["alarms"].configure(text=str(self.n_alarms), foreground=ERRC)
                self.root.bell()
            row = (m["sent_at"][11:19], "Active" if ongoing else "Ended", zone_en(p["roi"]),
                   f"{p['score']:.2f}", " / ".join(reason_en(r) for r in p["reasons"]))
            if self.tv.exists(eid):
                self.tv.item(eid, values=row, tags=("on",) if ongoing else ())
            else:
                self.tv.insert("", 0, iid=eid, values=row, tags=("on",) if ongoing else ())
            self._watching()

    def _poll(self):
        try:
            for _ in range(500):
                kind, d = self.q.get_nowait()
                if kind == "msg":
                    self._on_msg(d)
                elif kind == "log":
                    self._log(log_en(d))
                    if d.startswith("레이더 연결 끊김"):
                        self.disconnected = True
                        self.s["person"].configure(text="Unknown")
                        self._watching()
                    elif d == "레이더 다시 연결됨":
                        self.disconnected = False
                        self._watching()
                    elif d.endswith("연결하는 중"):
                        self._set_banner("busy", "Connecting to radar", log_en(d))
                elif kind == "learning":
                    self.learn_left = float(d)
                    self._learn_tick()
                elif kind == "running":
                    self.learn_left = 0
                    self._watching()
                elif kind == "stats":
                    self.s["radar"].configure(text="Connected" if d["connected"] else "Disconnected",
                                              foreground=OKC if d["connected"] else ERRC)
                    self.s["fps"].configure(text=f"{d['fps']:.1f}")
                    if d["sent_ok"] is not None:
                        fail = d["sent_fail"]
                        self.s["server"].configure(text=f"{d['sent_ok']} sent · {fail} failed",
                                                   foreground=ERRC if fail else OKC)
                        if fail and d["send_error"] and d["send_error"] != getattr(self, "_last_send_err", ""):
                            self._last_send_err = d["send_error"]
                            self._log(f"Server send failed: {d['send_error']}")
                elif kind == "server_check":
                    ok, text = d
                    self.server_note.configure(text=text, foreground=OKC if ok else ERRC)
                elif kind == "error":
                    self._log(f"Error: {log_en(d)}")
                    self._set_banner("err", "Stopped: error", log_en(d))
                    messagebox.showerror("Detection error", log_en(d))
                elif kind == "done":
                    self.learn_left = 0
                    self.s["radar"].configure(text="—", foreground="")
                    self.s["fps"].configure(text="—")
                    self.s["person"].configure(text="—")
                    self.disconnected = False
                    self._enable_inputs(True)
                    if self.b_title.cget("text") != "Stopped: error":
                        self.open_events.clear()
                        self._set_banner("idle", "Stopped", f"{self.n_alarms} alert(s). Select Start live detection to run again.")
        except queue.Empty:
            pass
        self.root.after(100, self._poll)


def main():
    try:  # Windows 고해상도 화면에서 글자가 흐리지 않게 (창을 만들기 전에)
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    load_bundled_fonts()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
