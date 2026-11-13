"""판단 하나를 융합 규칙에 넣고 그 결과를 경보로 저장한다."""

from datetime import datetime, timedelta

from rafi.common.messages import AlertCreatedMessage, AlertUpdatedMessage, Detection
from rafi.fusion.rules import fuse
from rafi.server.config import Config
from rafi.server.live import server_envelope
from rafi.server.storage import Storage


def record_detection(
    storage: Storage, detection: Detection, room: str, config: Config, now: datetime
) -> list[AlertCreatedMessage | AlertUpdatedMessage]:
    """경보를 저장하고, 대시보드에 알릴 메시지를 돌려준다."""
    open_alerts = storage.open_alerts(room)
    existing = {alert.alert_id: alert.detections for alert in open_alerts}
    window = timedelta(seconds=config.fusion_window_sec)

    messages: list[AlertCreatedMessage | AlertUpdatedMessage] = []
    for change in fuse(detection, open_alerts, window):
        linked = [*existing.get(change.alert_id, []), *change.detections]
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
