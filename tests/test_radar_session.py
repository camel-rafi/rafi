"""레이더 실시간 화면의 칸 설정 덮어쓰기 (session.active_stall, gui.to_overrides)."""

import json

import pytest

from rafi.radar.session import REPO, Settings, active_stall

STALL = REPO / "config" / "radar" / "stall_1105.json"


def test_no_override_uses_original(tmp_path):
    s = Settings(stall=str(STALL), out_dir=str(tmp_path))
    assert active_stall(s) == str(STALL)


def test_override_writes_copy_and_keeps_zones(tmp_path):
    s = Settings(stall=str(STALL), out_dir=str(tmp_path), overrides={"pitch_deg": 35.0, "radar_pos": [0.6, 0.2, 2.1]})
    out = json.loads(open(active_stall(s), encoding="utf-8").read())
    orig = json.loads(STALL.read_text(encoding="utf-8"))
    assert out["pitch_deg"] == 35.0 and out["radar_pos"] == [0.6, 0.2, 2.1]
    assert out["zones"] == orig["zones"]  # 비품 구역은 그대로
    assert json.loads(STALL.read_text(encoding="utf-8"))["pitch_deg"] == orig["pitch_deg"]  # 원본은 안 바뀜


def test_gui_overrides_only_changed_values():
    gui = pytest.importorskip("rafi.radar.gui")  # tkinter 없는 환경이면 건너뜀
    base = gui.read_pose(str(STALL))
    assert gui.to_overrides(dict(base), base) == {}
    changed = {**base, "pitch_deg": 35.0, "pos_z": 2.1}
    ov = gui.to_overrides(changed, base)
    assert ov["pitch_deg"] == 35.0 and ov["radar_pos"][2] == 2.1 and set(ov) == {"pitch_deg", "radar_pos"}


def test_gui_english_reasons():
    gui = pytest.importorskip("rafi.radar.gui")
    cases = {
        "디스펜서 앞·아래 손 머묾 3.2초": "Hand held at Dispenser (front/below) for 3.2 s",
        "위생함 앞·아래 손 머묾 5.1초 (손 떨림)": "Hand held at Sanitary bin (front/below) for 5.1 s (hand tremor)",
        "선반 아래 선 머리 3.4초 (머리 1.41 m, 뒷벽에서 0.25 m)":
            "Standing head under shelf for 3.4 s (head 1.41 m, 0.25 m from back wall)",
        "서 있던 높이(1.58 m)보다 0.31 m 높은 상태 6.0초 (올라섬)": "0.31 m above standing height (1.58 m) for 6.0 s (climbed up)",
    }
    for ko, en in cases.items():
        assert gui.reason_en(ko) == en
    assert gui.log_en("탐지 시작") == "Detection started"
    assert gui.log_en("192.168.30.1:29172에 연결하는 중") == "Connecting to 192.168.30.1:29172"
