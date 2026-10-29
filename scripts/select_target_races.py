from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from live_common import deadline_datetime, normalize_now


STOP_STATUSES = {"finished", "cancelled", "canceled", "aborted"}
NARUTO_BACKFILL_BATCH_SIZE = 4


def _naruto_final_missing(
    payload: dict[str, Any],
    race: dict[str, Any],
    race_no: int,
    output_root: Path,
) -> bool:
    marker = output_root / payload["date"] / "naruto" / f"{race_no:02d}" / "prediction_status.json"
    if marker.is_file():
        try:
            import json
            status = json.loads(marker.read_text(encoding="utf-8"))
            if status.get("status") == "complete" and status.get("stage") == "live":
                return False
        except (OSError, ValueError):
            pass
    prediction = (payload.get("preds") or {}).get(str(race_no)) or race.get("prediction") or {}
    return not (
        prediction.get("phase") == "final"
        and prediction.get("predictionStage") == "live"
        and prediction.get("active_prediction_stage") == "live"
    )


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
        post_deadline_window = close_at <= current < close_at + result_monitor
        naruto_live_backfill = (
            venue["slug"] == "naruto"
            and output_root is not None
            and post_deadline_window
            and _naruto_final_missing(payload, race, race_no, output_root)
        )
        # Naruto morning data can arrive after an early race has closed. Keep
        # polling its LIVE inputs in the existing result window until a
        # verified final prediction has actually been written.
        fetch_live = fetch_live or naruto_live_backfill
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
            and post_deadline_window
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
                    "naruto_live_backfill": naruto_live_backfill,
                }
            )
    if venue["slug"] == "naruto":
        regular = [target for target in targets if not target["naruto_live_backfill"]]
        backfill = [target for target in targets if target["naruto_live_backfill"]]
        return regular + backfill[:NARUTO_BACKFILL_BATCH_SIZE]
    return targets
