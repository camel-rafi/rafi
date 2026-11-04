"""모듈 간에 주고받는 판단 결과 형식.

레이더와 RF 모듈은 원데이터를 공유하지 않고, 각자 판단한 결과만 이 형식으로 융합 모듈에 보낸다.
개인 식별 정보(MAC 주소 원문, 사람 위치 궤적 등)는 이 형식에 담지 않는다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Source(StrEnum):
    RADAR = "radar"
    RF = "rf"


@dataclass(frozen=True)
class Detection:
    """한 모듈이 내린 하나의 의심 판단."""

    source: Source
    timestamp: datetime  # 공통 시각 기준(UTC 권장). 모든 장비는 NTP로 시각을 맞춘다.
    score: float  # 0.0 ~ 1.0 의심 점수
    roi: str | None = None  # 관심 구역 이름 (예: "vent", "shelf"). RF는 보통 None
    reasons: tuple[str, ...] = field(default_factory=tuple)  # 충족된 규칙 (대시보드 판단 근거)
