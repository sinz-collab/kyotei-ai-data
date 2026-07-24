from __future__ import annotations

import argparse
import json
from pathlib import Path


PREDICTION_VENUES = {
    "toda",
    "wakamatsu",
    "shimonoseki",
    "heiwajima",
    "tokoname",
    "ashiya",
    "omura",
    "karatsu",
}
EMPTY = (None, "", [], {})


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def has_value(value: object) -> bool:
    return value not in EMPTY


def key_count(value: object) -> int:
    if isinstance(value, dict):
        return len(value) + sum(key_count(item) for item in value.values())
    if isinstance(value, list):
        return sum(key_count(item) for item in value)
    return 0


def tickets(prediction: dict) -> list:
    found = []
    for key in ("ai", "aiUpset", "balance", "tickets"):
        value = prediction.get(key)
        if isinstance(value, list):
            found.extend(value)
    return found


def meaningful_result(prediction: dict) -> bool:
    result = prediction.get("result")
    return isinstance(result, dict) and result.get("status") == "ok"


def meaningful_live(prediction: dict) -> bool:
    realtime = prediction.get("realtime")
    return isinstance(realtime, dict) and any(has_value(value) for value in realtime.values())


def meaningful_odds(prediction: dict) -> bool:
    odds = prediction.get("odds")
    return isinstance(odds, dict) and bool(odds)


def compare_prediction_domains(slug: str, before: dict, after: dict, errors: list[str]) -> None:
    before_predictions = before.get("preds") or {}
    after_predictions = after.get("preds") or {}
    if before_predictions and not after_predictions:
        errors.append(f"{slug}: prediction became empty")
        return

    before_ticket_count = sum(len(tickets(prediction)) for prediction in before_predictions.values())
    after_ticket_count = sum(len(tickets(prediction)) for prediction in after_predictions.values())
    if before_ticket_count and not after_ticket_count:
        errors.append(f"{slug}: tickets disappeared from every race")

    for race_no, previous in before_predictions.items():
        current = after_predictions.get(race_no) or {}
        if meaningful_result(previous) and not meaningful_result(current):
            errors.append(f"{slug} {race_no}R: confirmed result disappeared")
        if meaningful_live(previous) and not meaningful_live(current):
            errors.append(f"{slug} {race_no}R: live data disappeared")
        if meaningful_odds(previous) and not meaningful_odds(current):
            errors.append(f"{slug} {race_no}R: odds disappeared")


def validate(before_root: Path, after_root: Path) -> list[str]:
    errors: list[str] = []
    before_manifest = load_json(before_root / "manifest.json")
    after_manifest = load_json(after_root / "manifest.json")
    prediction_days = []

    before_live = {
        path.relative_to(before_root / "live")
        for path in (before_root / "live").rglob("*")
        if path.is_file()
    } if (before_root / "live").is_dir() else set()
    after_live = {
        path.relative_to(after_root / "live")
        for path in (after_root / "live").rglob("*")
        if path.is_file()
    } if (after_root / "live").is_dir() else set()
    missing_live = sorted(str(path) for path in before_live - after_live)
    if missing_live:
        errors.append(f"data/live files disappeared: {missing_live[:10]}")

    before_venues = {venue["slug"]: venue for venue in before_manifest.get("venues") or []}
    for venue in after_manifest.get("venues") or []:
        slug = venue.get("slug", "")
        if not venue.get("open"):
            continue
        path_text = venue.get("dataPath")
        if not path_text:
            errors.append(f"{slug}: open venue has no dataPath")
            continue
        after_path = after_root / path_text
        before_path = before_root / (before_venues.get(slug, {}).get("dataPath") or path_text)
        after = load_json(after_path)
        before = load_json(before_path) if before_path.is_file() else {}

        if slug not in PREDICTION_VENUES:
            if venue.get("predictionStatus") != "unavailable":
                errors.append(f"{slug}: unregistered venue is not marked unavailable")
            continue

        if venue.get("predictionStatus") != "ready":
            continue
        day = after.get("eventDay")
        if isinstance(day, int) and day > 0:
            prediction_days.append(day)
        if after.get("engine") == "deterministic_baseline_v1":
            errors.append(f"{slug}: engine changed to deterministic_baseline_v1")
        if not after.get("preds"):
            errors.append(f"{slug}: prediction is empty")
        if isinstance(day, int) and day > 1:
            for race in after.get("races") or []:
                lanes = [
                    racer.get("lane")
                    for racer in race.get("racers") or []
                    if racer.get("season_runs")
                ]
                if sorted(lanes) != list(range(1, 7)):
                    errors.append(f"{slug} {race.get('race')}R: setsukan missing lanes")
        compare_prediction_domains(slug, before, after, errors)
        before_keys = key_count(before)
        after_keys = key_count(after)
        if before_keys >= 100 and after_keys < before_keys * 0.75:
            errors.append(f"{slug}: key count dropped {before_keys} -> {after_keys}")

    if len(prediction_days) > 1 and all(day == 1 for day in prediction_days):
        errors.append("all prediction venues became day 1")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    args = parser.parse_args()
    errors = validate(args.before, args.after)
    print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
