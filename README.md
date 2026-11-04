# RaFi

레이더–RF 융합 기반 불법촬영기기 설치 행위 탐지 시스템

2026-2학기 캡스톤디자인 (손진호 교수님) · 캐몰팀 · 수행기간 2026.09.01 ~ 2026.12.21

## 개요

4D 이미징 레이더(Retina-4SN)로 카메라 설치 행동을 포착하고, Wi-Fi 트래픽 관찰로 카메라 존재를 보강한 뒤,
두 결과를 시간 기준으로 결합해 관리자 대시보드에 L1/L2/L3 등급 경보를 띄웁니다.

```
레이더 (PC)  ── 설치 의심 판단 ──┐
                                 ├─▶ 융합 (시간창 결합, 등급) ─▶ 대시보드
RF (Pi 5)   ── 카메라 후보 판단 ─┘
```

| 등급 | 조건 | 조치 |
| --- | --- | --- |
| L1 | 레이더만 반응 | 확인 요청 |
| L2 | 레이더 + RF 반응 | 우선 확인 요청 |
| L3 | RF만 반응 | 기존 전파 탐지 수준 알림 |

## 팀

| 이름 | 역할 |
| --- | --- |
| 여경빈 | 팀장 |
| 김준원 | |
| 정희선 | |
| 천서한 | |

역할(담당 모듈)은 첫 회의에서 정해 채워 주세요.

## 저장소 구조

```
src/rafi/
  common/     공통 이벤트 형식, 시간 처리 (모든 모듈이 사용)
  radar/      포인트 클라우드 전처리, 특징 추출, 규칙 기반 판단 (PC)
  rf/         무선랜 프레임 캡처, 기기별 통계, 카메라 후보 판단 (Raspberry Pi 5)
  fusion/     시간창 기반 사건 묶기, L1/L2/L3 등급 판단
  dashboard/  관리자 대시보드
tests/        자동 테스트 (CI에서 실행)
experiments/  시나리오 정의, 실험 설정
docs/         설계 문서, 실험 프로토콜
data/         로컬 데이터 위치 (Git에 올리지 않음, data/README.md 참고)
scripts/      저장소 관리 스크립트
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
pip install -e ".[rf]"              # RF 담당 (Raspberry Pi 5)
pip install -e ".[dev,dashboard]"   # 대시보드 담당
```

환경 변수는 `.env.example`을 복사해 `.env`로 만들어 채웁니다. `.env`는 Git에 올라가지 않습니다.

## 협업 규칙

- 모든 작업은 이슈부터 만들고, 시작할 때 본인을 Assignee로 지정합니다.
- 브랜치: `feature/이슈번호-설명`, `fix/...`, `chore/...`, `docs/...`, `exp/...`(실험)
- 모든 변경은 PR로 올리고, 리뷰 승인 1명 + CI 통과 후 병합합니다.
- PR 설명에 `Closes #이슈번호`를 적어 이슈와 연결합니다.

## 데이터와 연구 윤리

- 레이더 녹화(`.pcr`), 패킷 캡처(`.pcap`) 등 실험 데이터는 **Git에 올리지 않습니다.** 팀 공유 드라이브에 보관하고 위치는 `data/README.md`에 적습니다.
- RF 실험은 격리된 시험 네트워크에서 팀 소유 기기로만 수행하며, 등록된 실험 기기 외의 프레임은 캡처 단계에서 폐기합니다.
- 대시보드와 로그에는 등급, 관심 구역, 시각, 판단 근거만 남기고 개인 식별 정보는 남기지 않습니다.
