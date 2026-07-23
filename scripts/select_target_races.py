from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from live_common import deadline_datetime, normalize_now


STOP_STATUSES = {"finished", "cancelled", "canceled", "aborted"}


def select_target_races(
    venue: dict[str, Any],
    now: datetime,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    current = normalize_now(now, config)
    payload = venue["payload"]
    if payload.get("date") != current.date().isoformat():
        return []
    targets = []
    monitor = timedelta(minutes=config["race_monitor_minutes_before_deadline"])
    for race in payload.get("races", []):
        race_no = int(race.get("race") or 0)
        deadline = str(race.get("deadline") or "")
        state = str(race.get("status") or "").lower()
        if race_no not in range(1, 13) or state in STOP_STATUSES or race.get("cancelled") is True:
            continue
        close_at = deadline_datetime(payload["date"], deadline, config)
        if close_at - monitor <= current < close_at:
            targets.append(
                {
                    "venue": venue["slug"],
                    "venue_name": venue["name"],
                    "date": payload["date"],
                    "race_no": race_no,
                    "deadline": deadline,
                    "deadline_at": close_at,
                    "racers": race.get("racers") or [],
                }
            )
    return targets
