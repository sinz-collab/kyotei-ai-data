#!/usr/bin/env python3
"""Protect prior predictions and isolate predictions generated after deadline."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")


def load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def has_prediction(value: object) -> bool:
    return isinstance(value, dict) and any(
        value.get(key) for key in ("normalTickets", "flyTickets", "tickets", "ranking")
    )


def race_map(payload: dict | None) -> dict[int, dict]:
    if not payload:
        return {}
    return {
        int(race.get("race") or 0): race
        for race in payload.get("races") or []
        if isinstance(race, dict) and int(race.get("race") or 0) in range(1, 13)
    }


def deadline_passed(target_date: str, deadline: str, now: datetime) -> bool:
    try:
        value = datetime.strptime(f"{target_date} {deadline}", "%Y-%m-%d %H:%M")
    except ValueError:
        return True
    return value.replace(tzinfo=JST) <= now


def protect_payload(current: dict, previous: dict | None, scope: str, now: datetime) -> dict:
    target_date = str(current.get("date") or "")
    previous_preds = (previous or {}).get("preds") or {}
    current_preds = current.get("preds") or {}
    previous_races = race_map(previous)
    current_races = race_map(current)
    post_deadline: list[int] = []

    for race_no, race in current_races.items():
        key = str(race_no)
        prior_race = previous_races.get(race_no, {})
        prior_prediction = previous_preds.get(key)
        if has_prediction(prior_prediction):
            current_preds[key] = copy.deepcopy(prior_prediction)
            if has_prediction(prior_race.get("prediction")):
                race["prediction"] = copy.deepcopy(prior_race["prediction"])
            continue

        generated = current_preds.get(key)
        if not has_prediction(generated):
            continue
        if not deadline_passed(target_date, str(race.get("deadline") or ""), now):
            continue

        post_deadline.append(race_no)
        marker = {
            "generationContext": "manual_recovery_post_deadline",
            "performanceEligible": False,
            "excludeFromPerformanceMetrics": True,
        }
        generated.update(marker)
        generated.pop("prediction_history", None)
        native = race.get("prediction")
        if isinstance(native, dict):
            native.update(marker)
            native.pop("prediction_history", None)

    current["preds"] = current_preds
    current["manualRecovery"] = {
        "scope": scope,
        "processedAt": now.isoformat(timespec="seconds"),
        "postDeadlineRaces": post_deadline,
        "excludePostDeadlineFromPerformanceMetrics": True,
        "priorPredictionsPreserved": True,
    }
    return current


def write_atomic(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--scope", choices=("all", "failed"), required=True)
    parser.add_argument("--now", help="ISO timestamp used by tests")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now).astimezone(JST) if args.now else datetime.now(JST)
    compact = args.date.replace("-", "")

    for path in sorted((args.after / "venues").glob(f"*/{compact}.json")):
        relative = path.relative_to(args.after)
        current = load(path)
        if not current or current.get("date") != args.date:
            continue
        previous = load(args.before / relative)
        protected = protect_payload(current, previous, args.scope, now)
        write_atomic(path, protected)
        latest = path.with_name("latest.json")
        latest_payload = load(latest)
        if latest_payload and latest_payload.get("date") == args.date:
            write_atomic(latest, copy.deepcopy(protected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
