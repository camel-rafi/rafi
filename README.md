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
  dashboard/  관리자 대시보드
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

pip install -e ".[dev]"      # 공통 + 테스트 도구
pytest                       # 테스트가 통과하면 세팅 완료
```

맡은 모듈에 따라 추가 의존성을 설치합니다.

```bash
pip install -e ".[dev,radar]"       # 레이더 담당 (PC)
pip install -e ".[rf]"              # RF 담당 (Raspberry Pi)
pip install -e ".[dev,dashboard]"   # 대시보드 담당
```

환경 변수는 `.env.example`을 복사해 `.env`로 만들어 채웁니다. `.env`는 Git에 올라가지 않습니다.

## 협업 규칙

- `main`에는 PR로만 반영하고, CI 통과 후 병합합니다.
- 브랜치: `feature/설명`, `fix/...`, `chore/...`, `docs/...`, `exp/...`(실험)

## 데이터와 연구 윤리

- 레이더 녹화(`.pcr`), 패킷 캡처(`.pcap`) 등 실험 데이터는 **Git에 올리지 않습니다.** 팀 공유 드라이브에 보관하고 위치는 `data/README.md`에 적습니다.
- RF 실험은 격리된 시험 네트워크에서 팀 소유 기기로만 수행하며, 등록된 실험 기기 외의 프레임은 캡처 단계에서 폐기합니다.
- 대시보드와 로그에는 등급, 관심 구역, 시각, 판단 근거만 남기고 개인 식별 정보는 남기지 않습니다.
