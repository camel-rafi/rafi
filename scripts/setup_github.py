"""GitHub 저장소에 라벨, 마일스톤, 초기 이슈(와 선택적으로 프로젝트 보드)를 한 번에 만든다.

사전 준비:
  1. GitHub CLI 설치: https://cli.github.com
  2. gh auth login
  3. (보드도 만들 경우) gh auth refresh -s project

사용법 (저장소 폴더에서):
  python scripts/setup_github.py                 # 라벨 + 마일스톤 + 이슈
  python scripts/setup_github.py --project       # + 프로젝트 보드 생성 및 이슈 추가
  python scripts/setup_github.py --clean-labels  # GitHub 기본 라벨 중 안 쓰는 것 삭제
  python scripts/setup_github.py --dry-run       # 실행할 명령만 출력

여러 번 실행해도 이미 있는 라벨/마일스톤/이슈(같은 제목)는 건너뛴다.
"""

import argparse
import json
import subprocess
import sys

OWNER = "camel-rafi"
REPO = "rafi"

LABELS = [
    # 종류
    ("feature", "1D76DB", "새 기능 구현"),
    ("bug", "D73A4A", "잘못 동작하는 것"),
    ("experiment", "FBCA04", "데이터 수집, 측정, 비교 실험"),
    ("chore", "C5DEF5", "설정, 환경, 리팩터링"),
    ("docs", "0075CA", "문서, 보고서, 발표"),
    ("urgent", "B60205", "바로 처리해야 하는 것"),
    # 담당 영역
    ("area:radar", "5319E7", "레이더 모듈"),
    ("area:rf", "0E8A16", "RF 모듈"),
    ("area:fusion", "D93F0B", "융합, 등급 경보"),
    ("area:dashboard", "006B75", "대시보드"),
    ("area:infra", "BFD4F2", "저장소, CI, 장비, 네트워크"),
]

GITHUB_DEFAULT_LABELS_TO_REMOVE = [
    "documentation", "duplicate", "enhancement", "good first issue",
    "help wanted", "invalid", "question", "wontfix",
]

# (제목, 마감일, 설명) 마감일은 제안값. 수업 일정에 맞게 고쳐서 실행하세요.
MILESTONES = [
    ("M0 프로젝트 준비", "2026-10-10", "개발 환경, 장비 세팅, 데이터 형식 조사"),
    ("M1 실험 환경 구축", "2026-10-24", "실험 공간, 시나리오 프로토콜, 격리 네트워크, 1차 녹화"),
    ("M2 모듈별 1차 구현", "2026-11-14", "레이더 규칙 판단기, RF 카메라 후보 판단기"),
    ("M3 융합 및 대시보드", "2026-11-28", "시간창 결합, 등급 경보, 모듈 간 통신, 대시보드"),
    ("M4 검증 및 평가", "2026-12-12", "재생 기반 평가, 단독/융합 비교"),
    ("M5 최종 발표", "2026-12-21", "보고서, 발표, 시연"),
]

DONE = "\n\n## 완료 조건\n"


def body(desc: str, *checks: str) -> str:
    return desc + DONE + "\n".join(f"- [ ] {c}" for c in checks)


# (제목, 라벨들, 마일스톤, 본문)
ISSUES = [
    # M0
    ("팀원 개발 환경 세팅", ["chore", "area:infra"], "M0 프로젝트 준비",
     body("README의 '개발 환경 세팅'을 따라 각자 로컬 환경을 만든다.",
          "4명 모두 `pytest` 통과", "막힌 부분은 README에 보완")),
    ("모듈별 담당자와 협업 규칙 확정", ["docs"], "M0 프로젝트 준비",
     body("첫 회의에서 레이더/RF/융합/대시보드 담당을 정하고 README 팀 표를 채운다.",
          "README 팀 표에 역할 기입", "주간 회의 요일 확정", "Ruleset 승인 수 1로 변경")),
    ("Retina-4SN / Radar Studio 데이터 형식 조사", ["chore", "area:radar"], "M0 프로젝트 준비",
     body("녹화 파일(.pcr) 구조와 실시간 수신 방식(필드: x, y, z, doppler, power, targetId)을 확인하고 "
          "Python에서 읽는 방법을 정리한다. 제조사 문서/SDK를 우선 확인한다.",
          "docs/radar-data-format.md 작성", "샘플 파일을 Python으로 읽어 필드 출력")),
    ("Raspberry Pi 5 + 모니터 모드 어댑터 세팅", ["chore", "area:rf", "area:infra"], "M0 프로젝트 준비",
     body("OS 설치, 원격 접속(SSH), 어댑터 드라이버와 모니터 모드 동작을 확인한다.",
          "모니터 모드 전환 절차 문서화", "Pi에서 `pip install -e \".[rf]\"` 성공")),
    ("실험 데이터 공유 드라이브와 파일 이름 규칙 정하기", ["chore", "area:infra"], "M0 프로젝트 준비",
     body("Git에 올리지 않는 녹화/캡처 데이터를 보관할 위치를 정한다.",
          "data/README.md에 위치 링크 기입", "파일 이름 규칙 합의")),
    # M1
    ("실험 공간 구성과 레이더 설치 위치·ROI 정의", ["experiment", "area:radar"], "M1 실험 환경 구축",
     body("화장실 칸(또는 모사 공간)에 레이더를 천장 모서리에 설치하고 환풍구·선반·벽면 상단 ROI 좌표를 정한다.",
          "설치 사진과 치수 기록", "ROI 좌표를 설정 파일로 저장")),
    ("실험 시나리오 프로토콜 문서화", ["docs", "experiment"], "M1 실험 환경 구축",
     body("설치/정상/혼동/교란 시나리오별 수행 절차, 참가자 안내, 반복 횟수(20회 이상)를 정한다.",
          "docs/experiment-protocol.md 작성", "experiments/scenarios/scenarios.md 확정")),
    ("레이더 시나리오 1차 녹화", ["experiment", "area:radar"], "M1 실험 환경 구축",
     body("Radar Studio로 시나리오별 .pcr을 녹화한다. 먼저 시나리오당 소량으로 파이프라인을 검증한다.",
          "모든 시나리오 최소 3회 녹화", "공유 드라이브에 규칙대로 저장")),
    ("격리 시험 네트워크 구성과 실험 기기 등록", ["chore", "area:rf"], "M1 실험 환경 구축",
     body("팀 소유 AP와 기기로 격리 네트워크를 만들고, 실험 기기(카메라, 휴대폰 등) MAC 목록을 정리한다.",
          "실험 기기 목록 문서화", "RF_ALLOWED_MACS 설정")),
    ("RF 캡처와 미등록 기기 프레임 폐기 필터", ["feature", "area:rf"], "M1 실험 환경 구축",
     body("모니터 모드로 헤더 정보(송신 주소, 프레임 크기, 시각)만 수집하고, 등록 기기 외 프레임은 캡처 단계에서 버린다.",
          "필터 동작 테스트", "저장 형식 결정")),
    ("카메라 전원 인가 ~ 통신 시작 지연 실측", ["experiment", "area:rf"], "M1 실험 환경 구축",
     body("융합 시간창 크기를 정하기 위해 실험용 카메라별 지연을 측정한다.",
          "카메라별 10회 이상 측정", "결과로 fusion DEFAULT_WINDOW 제안")),
    # M2
    ("포인트 클라우드 로더와 targetId별 시계열 구성", ["feature", "area:radar"], "M2 모듈별 1차 구현",
     body("녹화 파일을 읽어 사람(targetId)별 프레임 시계열로 만든다.", "로더 테스트 작성")),
    ("레이더 프레임·시간 특징 추출", ["feature", "area:radar"], "M2 모듈별 1차 구현",
     body("상위 z 백분위, 중심 이동량, ROI 내부 점 비율, 도플러 분포, ROI 체류 시간, '팔 높음 + 몸통 정지' 지속 시간.",
          "특징 함수별 테스트", "시나리오별 특징 분포 그래프")),
    ("규칙 기반 설치 의심 판단기와 임계값 튜닝", ["feature", "area:radar"], "M2 모듈별 1차 구현",
     body("(몸통 이동량 < 임계값) AND (팔 높이 > 기준) AND (ROI 체류 >= T초). 녹화 데이터로 임계값을 정한다.",
          "판단 결과를 Detection으로 출력", "판단 근거(reasons) 포함")),
    ("RF 기기별 트래픽 통계", ["feature", "area:rf"], "M2 모듈별 1차 구현",
     body("프레임 크기 분포, 전송 간격 평균/분산, 초당 업링크량, 연속 송신 지속 시간.", "통계 함수 테스트")),
    ("RF 카메라 후보 판단 1단계 규칙", ["feature", "area:rf"], "M2 모듈별 1차 구현",
     body("새 기기가 일정 업링크량 이상을 일정 시간 이상 지속하면 후보로 표시한다.",
          "카메라/휴대폰 트래픽으로 검증", "Detection으로 출력")),
    ("MAC 주소 무작위화로 인한 오탐 대응", ["feature", "area:rf"], "M2 모듈별 1차 구현",
     body("주소 유지 시간과 트래픽 패턴을 함께 고려해 휴대폰 오탐을 줄인다.", "전후 오탐 비교")),
    # M3
    ("장비 간 시각 동기화와 판단 결과 전송", ["feature", "area:fusion", "area:infra"], "M3 융합 및 대시보드",
     body("PC와 Pi의 시각을 NTP로 맞추고, 각 모듈의 Detection을 융합 모듈로 전송한다.",
          "시각 오차 측정", "전송 방식 결정 및 구현")),
    ("시간창 사건 묶기와 빈 방 보조 단서", ["feature", "area:fusion"], "M3 융합 및 대시보드",
     body("src/rafi/fusion/grading.py의 기본 틀에 실측 시간창과 재실 정보 가중치를 반영한다.",
          "빈 방 단서 테스트 추가")),
    ("대시보드 기술 스택 결정과 구현", ["feature", "area:dashboard"], "M3 융합 및 대시보드",
     body("등급, 관심 구역, 시각, 판단 근거만 표시하고 개인 식별 정보는 남기지 않는다.",
          "기술 스택 결정", "실시간 경보 표시", "시연 가능")),
    # M4
    ("Radar Emulator 재생 기반 평가 파이프라인", ["experiment", "area:radar"], "M4 검증 및 평가",
     body("녹화 데이터를 반복 재생해 같은 조건에서 규칙과 임계값을 비교한다.", "재생 → 판단 → 결과 저장 자동화")),
    ("평가 지표 계산", ["feature", "experiment"], "M4 검증 및 평가",
     body("탐지율(재현율), 시간당 오탐 횟수, 경보 지연, 교란 시나리오 동시 오반응 비율.", "지표 계산 스크립트")),
    ("레이더 단독 / RF 단독 / 융합 비교 실험", ["experiment", "area:fusion"], "M4 검증 및 평가",
     body("같은 시나리오에서 세 방식의 지표를 비교한다.", "비교표와 그래프", "결과 해석 문서화")),
    # M5
    ("최종 보고서 작성", ["docs"], "M5 최종 발표", body("", "초안", "교수님 피드백 반영", "제출")),
    ("최종 발표 자료와 시연 준비", ["docs"], "M5 최종 발표", body("", "발표 자료", "시연 시나리오 리허설", "시연 영상 백업")),
]

DRY = False


def gh(*args: str, capture: bool = True) -> str:
    cmd = ["gh", *args]
    if DRY:
        print("$", " ".join(cmd))
        return "[]" if capture else ""
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        print(f"  ! 실패: {' '.join(cmd[:4])} ...\n    {res.stderr.strip()}", file=sys.stderr)
        return ""
    return res.stdout.strip()


def main() -> None:
    global DRY
    p = argparse.ArgumentParser()
    p.add_argument("--repo", default=f"{OWNER}/{REPO}")
    p.add_argument("--project", action="store_true", help="프로젝트 보드도 생성")
    p.add_argument("--clean-labels", action="store_true", help="안 쓰는 기본 라벨 삭제")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    DRY = a.dry_run
    repo = a.repo
    owner = repo.split("/")[0]

    print("== 라벨")
    for name, color, desc in LABELS:
        gh("label", "create", name, "--repo", repo, "--color", color, "--description", desc, "--force")
        print("  ", name)
    if a.clean_labels:
        for name in GITHUB_DEFAULT_LABELS_TO_REMOVE:
            gh("label", "delete", name, "--repo", repo, "--yes")

    print("== 마일스톤")
    existing = gh("api", f"repos/{repo}/milestones?state=all&per_page=100")
    have = {m["title"] for m in json.loads(existing or "[]")}
    for title, due, desc in MILESTONES:
        if title in have:
            print("   (있음)", title)
            continue
        gh("api", f"repos/{repo}/milestones", "-f", f"title={title}",
           "-f", f"due_on={due}T23:59:59Z", "-f", f"description={desc}")
        print("  ", title, due)

    print("== 이슈")
    existing = gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "500", "--json", "title")
    have = {i["title"] for i in json.loads(existing or "[]")}
    urls = []
    for title, labels, milestone, text in ISSUES:
        if title in have:
            print("   (있음)", title)
            continue
        url = gh("issue", "create", "--repo", repo, "--title", title, "--body", text,
                 "--label", ",".join(labels), "--milestone", milestone)
        if url:
            urls.append(url)
        print("  ", title)

    if a.project:
        print("== 프로젝트 보드")
        out = gh("project", "create", "--owner", owner, "--title", "RaFi 개발 보드", "--format", "json")
        number = str(json.loads(out)["number"]) if out and not DRY else "N"
        gh("project", "link", number, "--owner", owner, "--repo", repo)
        for url in urls:
            gh("project", "item-add", number, "--owner", owner, "--url", url)
        print(f"   보드 #{number} 생성, 이슈 {len(urls)}개 추가. 웹에서 Board 보기로 바꿔 주세요.")

    print("완료")


if __name__ == "__main__":
    main()
