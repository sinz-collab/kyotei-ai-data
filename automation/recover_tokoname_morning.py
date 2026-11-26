#!/usr/bin/env python3
"""Publish native Tokoname morning predictions without touching existing LIVE predictions."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from automation.official_morning import atomic_json
from automation.publish_official_stages import update_index_files
from engines.tokoname_v1.tokoname_site_pipeline import (
    apply_tokoname_preliminary_predictions,
    tokoname_native_predictions_are_complete,
)


def run(date: str, data_root: Path) -> str:
    path = data_root / "venues" / "tokoname" / f"{date.replace('-', '')}.json"
    latest = path.parent / "latest.json"
    if not path.is_file():
        return "no_official_card"
    original = json.loads(path.read_text(encoding="utf-8"))
    if original.get("date") != date or len(original.get("races") or []) != 12:
        return "invalid_or_incomplete_card"
    if tokoname_native_predictions_are_complete(original):
        update_index_files(data_root, date, {"tokoname": original})
        return "already_ready"
    # Do not backfill after the first deadline or overwrite partial/LIVE results.
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    first = min(
        datetime.strptime(f"{date} {race['deadline']}", "%Y-%m-%d %H:%M")
        .replace(tzinfo=ZoneInfo("Asia/Tokyo"))
        for race in original["races"]
    )
    if now >= first:
        return "first_deadline_passed"
    if any(race.get("prediction") for race in original["races"]):
        return "partial_predictions_preserved"
    updated, reports = apply_tokoname_preliminary_predictions(original)
    if len(reports) != 12 or not all(r["status"] == "updated" for r in reports):
        raise RuntimeError("tokoname_preliminary_incomplete")
    if not tokoname_native_predictions_are_complete(updated):
        raise RuntimeError("tokoname_native_prediction_validation_failed")
    updated["predictionStatus"] = "ready"
    updated["predictionReason"] = ""
    # Only replace latest if it still reflects the same unpredicted morning card.
    if latest.is_file():
        current_latest = json.loads(latest.read_text(encoding="utf-8"))
        if current_latest.get("date") == date and not any(
            race.get("prediction") for race in current_latest.get("races") or []
        ):
            latest_updated, _ = apply_tokoname_preliminary_predictions(current_latest)
            if not tokoname_native_predictions_are_complete(latest_updated):
                raise RuntimeError("tokoname_latest_validation_failed")
            latest_updated["predictionStatus"] = "ready"
            latest_updated["predictionReason"] = ""
            atomic_json(latest, latest_updated)
    atomic_json(path, updated)
    update_index_files(data_root, date, {"tokoname": updated})
    return "published_12_races"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(json.dumps({"tokoname": run(args.date, args.data_root)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
