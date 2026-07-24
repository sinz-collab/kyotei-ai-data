from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from live_common import deadline_datetime, normalize_now


STOP_STATUSES = {"finished", "cancelled", "canceled", "aborted"}


def select_target_races(
    venue: dict[str, Any],
    now: datetime,
    config: dict[str, Any],
    output_root: Path | None = None,
) -> list[dict[str, Any]]:
    current = normalize_now(now, config)
    payload = venue["payload"]
    if payload.get("date") != current.date().isoformat():
        return []
    targets = []
    monitor = timedelta(minutes=config["race_monitor_minutes_before_deadline"])
    result_monitor = timedelta(minutes=config["result_monitor_minutes_after_deadline"])
    for race in payload.get("races", []):
        race_no = int(race.get("race") or 0)
        deadline = str(race.get("deadline") or "")
        state = str(race.get("status") or "").lower()
        if race_no not in range(1, 13) or state in STOP_STATUSES or race.get("cancelled") is True:
            continue
        close_at = deadline_datetime(payload["date"], deadline, config)
        fetch_live = close_at - monitor <= current < close_at
        result_path = (
            output_root / payload["date"] / venue["slug"] / f"{race_no:02d}" / "result.json"
            if output_root else None
        )
        result_complete = False
        if result_path and result_path.is_file():
            try:
                import json
                result_complete = json.loads(result_path.read_text(encoding="utf-8")).get("complete") is True
            except (OSError, ValueError):
                result_complete = False
        fetch_result = (
            output_root is not None
            and close_at <= current < close_at + result_monitor
            and not result_complete
        )
        if fetch_live or fetch_result:
            targets.append(
                {
                    "venue": venue["slug"],
                    "venue_name": venue["name"],
                    "date": payload["date"],
                    "race_no": race_no,
                    "deadline": deadline,
                    "deadline_at": close_at,
                    "racers": race.get("racers") or [],
                    "fetch_live": fetch_live,
                    "fetch_result": fetch_result,
                }
            )
    return targets
