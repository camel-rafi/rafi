# 레이더 분석 스크립트

레이더 판단 기준(머문 시간, 머리 높이 등)을 정하고 평가할 때 쓰는 스크립트다. 실시간 프로그램은 `src/rafi/radar/`에 있다.

## 준비

- `pip install -e ".[dev,radar]"`
- 녹화(.pcr)를 읽는 `pcr_loader`는 sg-radar-tools 저장소에 있다. `rafi`와 `sg-radar-tools` 폴더가 나란히 있으면 그대로 동작하고, 다른 곳에 있으면 환경 변수 `SG_RADAR_TOOLS`에 sg-radar-tools 경로를 넣는다.
- 녹화 파일은 Git에 없다. 팀 공유 드라이브에서 받아 `sg-radar-tools\recordings\` 아래에 둔다 (녹화 설명은 드라이브의 `recordings_README.md`).
- 결과는 `out\` 아래에 저장된다. Git에 올리지 않는다.

## 실행 (저장소 폴더에서)

| 스크립트 | 하는 일 |
|---|---|
| `rt_eval.py` | 녹화 폴더 전체를 실시간 판단 규칙으로 재생해 탐지·오경보를 센다 |
| `evaluate.py` | 구역별 머문 시간으로 기준값별 탐지율·오경보율을 비교한다 |
| `contacts.py` | 비품 구역에 머문 구간 후보와 확인용 그림을 만든다 |
| `door_motion.py` | 문 움직임을 사람 움직임과 구분해 본다 |
| `inventory.py` | 녹화 폴더 요약 (프레임 수, fps, 점 수) |
| `side_check.py` | 레이더 좌우 방향(mirror_x) 확인 |

```powershell
python experiments/radar/rt_eval.py ..\sg-radar-tools\recordings\record_1102 --stall config/radar/stall_1102.json
python experiments/radar/rt_eval.py ..\sg-radar-tools\recordings\record_1105 --stall config/radar/stall_1105.json
```

칸 설정은 `config/radar/stall_1102.json`(50°), `config/radar/stall_1105.json`(18°). 칸 실측과 분석 기록은 `docs/radar/`.

## 실시간 프로그램

### 화면으로 실행 (레이더 PC)

`scripts\radar_gui.bat`을 더블클릭하거나 `python -m rafi.radar.gui`. 입력칸을 확인하고 **Start live detection**을 누른다. 화면 글자는 영어이고, 서버로 보내는 메시지(근거 문장 포함)는 그대로 한국어다. 글씨체는 함께 넣어 둔 Pretendard(`src/rafi/radar/fonts`, OFL 1.1)라 따로 설치하지 않아도 된다.

| 칸 | 기본값 | 언제 바꾸나 |
|---|---|---|
| 레이더 입력 | 실제 레이더 `192.168.30.1:29172` | 녹화로 시험할 때 에뮬레이터 또는 녹화 파일 |
| 칸 설정 파일 | `config/radar/stall_1105.json` (우정원 7층 실험 칸) | 다른 칸에 설치할 때 |
| 칸 크기·레이더 위치·각도 | 설정 파일 값 | 레이더를 다시 달았을 때 (예: 35°). 원본은 그대로 두고 `out/rt/stall_active.json` 사본으로 탐지 |
| 배경 | 시작 후 20초 칸 비우고 배우기 | 녹화를 틀 때는 빈 칸 녹화 폴더 (입력을 바꾸면 자동으로 바뀜) |
| 서버 주소 | 비움 (안 보냄) | `http://서버IP:8000/api/v1/messages`. [서버 연결 확인]으로 먼저 확인 |

- 오른쪽 위 상태 아이콘: 회색 대기(Ready), 파랑 준비·배경 배우기, 주황 연결 끊김, 초록 감시 중(Monitoring), 빨강 레이더 의심(Suspicious activity, 구역과 근거)
- 마지막 입력값은 `out/rt/gui_settings.json`에 저장되어 다음에 그대로 뜬다
- 비품 구역(디스펜서·위생함 등) 위치는 설정 파일 값 그대로다. 칸이 바뀌면 설정 파일을 새로 만든다
- 화면과 아래 명령은 같은 탐지 코드(`src/rafi/radar/session.py`)를 쓴다

### 명령으로 실행

```powershell
# 녹화를 실시간처럼 재생
python -m rafi.radar.run --replay ..\sg-radar-tools\recordings\record_1102\danger_tissue_hs_1.pcr --bg-folder ..\sg-radar-tools\recordings\record_1102
# 실제 레이더 + 서버로 전송
python -m rafi.radar.run --host 192.168.30.1 --learn-bg 20 --post http://서버주소:8000/api/v1/messages
```
