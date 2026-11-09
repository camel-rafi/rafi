# RaFi

레이더–RF 융합 기반 불법촬영기기 설치 행위 탐지 시스템

2026-2학기 캡스톤디자인 (손진호 교수님) · 칸지킴이 · 수행기간 2026.09.01 ~ 2026.12.21

## 개요

4D 이미징 레이더(Retina-4SN)로 카메라 설치 행동을 포착하고, Wi-Fi 트래픽 관찰로 카메라 존재를 보강한 뒤,
두 결과를 중앙 서버에서 시간 기준으로 결합해 관리자 대시보드에 L1/L2/L3 등급 경보를 띄웁니다.

```
레이더 PC  ── HTTP: 설치 판단, 재실, 상태 ───────────────┐
                                                        ▼
                                  중앙 서버 (융합, 등급, 저장) ── WebSocket ─▶ 대시보드
                                                        ▲
Raspberry Pi ── HTTP: 기기별 1초 집계, 상태 ─▶ RF 판단 ─┘
```

| 등급 | 조건 | 조치 |
| --- | --- | --- |
| L1 | 레이더만 반응 | 확인 요청 |
| L2 | 레이더 + RF 반응 | 우선 확인 요청 |
| L3 | RF만 반응 | 기존 전파 탐지 수준 알림 |

## 팀

| 이름 | 역할 | 맡는 범위 |
| --- | --- | --- |
| 여경빈 | 레이더 모듈 | 레이더 판단, 실험 운영 |
| 김준원 | 레이더 성능 | 개선된 판단 모델 |
| 천서한 | RF 모듈 | Pi 프로그램, RF 판단 |
| 정희선 | 시스템 | 서버, 융합, 대시보드 |

## 저장소 구조

```
src/rafi/
  common/     메시지 규격 (모든 모듈이 사용)
  radar/      설치 행위 판단, 서버 전송 (레이더 PC)
  rf/         무선랜 프레임 캡처, 기기별 1초 집계, 서버 전송 (Raspberry Pi)
  fusion/     같은 사건 묶기, L1/L2/L3 등급 판단 (중앙 서버)
  server/     수신 API, 융합 호출, 저장소, 대시보드용 API와 WebSocket (중앙 서버)
  dashboard/  관리자 대시보드 (중앙 서버가 제공)
  sim/        가짜 송신기 (개발·시연용)
config/       서버가 인식하는 칸과 장비 목록
tests/        자동 테스트 (CI에서 실행)
experiments/  시나리오 정의, 실험 설정
data/         로컬 데이터 위치 (Git에 올리지 않음, data/README.md 참고)
```

## 개발 환경 세팅

Python 3.11 이상이 필요합니다.

```bash
git clone https://github.com/camel-rafi/rafi.git
cd rafi
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -e ".[dev,server]"   # 테스트 도구 + 중앙 서버
pytest                       # 테스트가 통과하면 세팅 완료
```

맡은 모듈에 따라 추가 의존성을 설치합니다.

```bash
pip install -e ".[dev,radar]"       # 레이더 담당 (PC)
pip install -e ".[rf]"              # RF 담당 (Raspberry Pi)
```

중앙 서버는 저장소 폴더에서 실행합니다.

```bash
uvicorn --factory rafi.server.app:create_app --host 0.0.0.0 --port 8000
```

실행 후 브라우저에서 `http://서버주소:8000/`을 열면 관리자 대시보드, `http://서버주소:8000/docs`를 열면 API 문서가 나옵니다.
칸, 구역, 장비, 청소 시간대는 `config/rafi.yaml`에서 관리하며, 바꾸면 서버를 재시작합니다.

## 시연

실제 모듈 없이 가짜 송신기로 경보 흐름을 재현할 수 있습니다. 서버를 켠 상태에서 다른 터미널을 열어 실행합니다.

```bash
python -m rafi.sim --list           # 시나리오 목록과 예상 결과
python -m rafi.sim rf-then-radar    # L3 알림이 L2로 바뀌는 과정
python -m rafi.sim demo             # 모든 시나리오를 차례로 (약 15분)
python -m rafi.sim demo --fast      # 기다리지 않고 바로 보내기
```

같은 시나리오를 2분 안에 다시 실행하면 앞의 경보와 같은 사건으로 묶입니다. 깨끗하게 다시 시작하려면 서버를 끄고 `data/rafi.db*` 파일을 지웁니다.

환경 변수는 `.env.example`을 복사해 `.env`로 만들어 채웁니다. `.env`는 Git에 올라가지 않습니다.

## 협업 규칙

- `main`에는 PR로만 반영하고, CI 통과 후 병합합니다.
- 브랜치: `feature/설명`, `fix/...`, `chore/...`, `docs/...`, `exp/...`(실험)

## 데이터와 연구 윤리

- 레이더 녹화(`.pcr`), 패킷 캡처(`.pcap`) 등 실험 데이터는 **Git에 올리지 않습니다.** 팀 공유 드라이브에 보관하고 위치는 `data/README.md`에 적습니다.
- RF 실험은 격리된 시험 네트워크에서 팀 소유 기기로만 수행하며, 등록된 실험 기기 외의 프레임은 캡처 단계에서 폐기합니다.
- 대시보드와 로그에는 등급, 관심 구역, 시각, 판단 근거만 남기고 개인 식별 정보는 남기지 않습니다.
