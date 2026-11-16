"""판단 하나를 융합 규칙에 넣고 그 결과를 경보로 저장한다."""

from datetime import datetime, timedelta

from rafi.common.messages import AlertCreatedMessage, AlertUpdatedMessage, Detection
from rafi.fusion.rules import AlertChange, OpenAlert, fuse, has_event
from rafi.server.config import Config
from rafi.server.live import server_envelope
from rafi.server.storage import Storage


def record_detection(
    storage: Storage, detection: Detection, room: str, config: Config, now: datetime
) -> list[AlertCreatedMessage | AlertUpdatedMessage]:
    """경보를 저장하고, 대시보드에 알릴 메시지를 돌려준다."""
    open_alerts = storage.open_alerts(room)
    window = timedelta(seconds=config.fusion_window_sec)
    changes = _continue_acked_event(storage, detection, room, open_alerts) or fuse(
        detection, open_alerts, window
    )

    messages: list[AlertCreatedMessage | AlertUpdatedMessage] = []
    for change in changes:
        existing = storage.detections_of(change.alert_id) if change.alert_id else []
        linked = [*existing, *change.detections]
        started_at = min(linked_detection.event_start for linked_detection in linked)
        alert_id = storage.save_alert(
            change,
            room,
            started_at,
            in_cleaning_window=config.in_cleaning_window(started_at),
            now=now,
        )
        alert = storage.get_alert(alert_id)
        if change.alert_id is None:
            messages.append(
                AlertCreatedMessage(type="alert.created", payload=alert, **server_envelope(now))
            )
        else:
            messages.append(
                AlertUpdatedMessage(type="alert.updated", payload=alert, **server_envelope(now))
            )
    return messages


def _continue_acked_event(
    storage: Storage, detection: Detection, room: str, open_alerts: list[OpenAlert]
) -> list[AlertChange] | None:
    """확인된 경보의 사건(event_id)이 이어지면 새 경보를 만들지 않고 그 경보에 붙인다.

    관리자가 이미 확인한 사건이므로 미확인으로 되돌리지 않고, 등급도 그대로 둔다.
    구역은 미확인 경보와 같이 최신 판단의 구역으로 바꾼다.
    """
    if detection.event_id is None:
        return None
    if any(has_event(alert, detection.event_id) for alert in open_alerts):
        return None  # 미확인 경보에 붙어 있으면 융합 규칙이 처리한다
    acked = storage.acked_alert_with_event(room, detection.event_id)
    if acked is None:
        return None
    alert_id, grade = acked
    return [AlertChange(alert_id, grade, detection.roi, [detection])]
