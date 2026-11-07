"""판단 하나를 융합 규칙에 넣고 그 결과를 경보로 저장한다."""

from datetime import datetime, timedelta

from rafi.common.messages import Detection
from rafi.fusion.rules import fuse
from rafi.server.config import Config
from rafi.server.storage import Storage


def record_detection(
    storage: Storage, detection: Detection, room: str, config: Config, now: datetime
) -> None:
    open_alerts = storage.open_alerts(room)
    existing = {alert.alert_id: alert.detections for alert in open_alerts}
    window = timedelta(seconds=config.fusion_window_sec)

    for change in fuse(detection, open_alerts, window):
        linked = [*existing.get(change.alert_id, []), *change.detections]
        started_at = min(linked_detection.event_start for linked_detection in linked)
        storage.save_alert(
            change,
            room,
            started_at,
            in_cleaning_window=config.in_cleaning_window(started_at),
            now=now,
        )
