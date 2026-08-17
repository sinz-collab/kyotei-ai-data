from __future__ import annotations

import argparse
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path


PREDICTION_VENUES = {
    "toda",
    "wakamatsu",
    "heiwajima",
    "tokoname",
    "ashiya",
    "omura",
    "karatsu",
    "biwako",
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


def normalize_racer_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\u3000]+", "", text)


def no_prior_meeting_runs_is_verified(
    after_root: Path,
    slug: str,
    target_date: str,
    event_day: int,
    racer: dict,
    setsukan: dict,
) -> bool:
    if (
        racer.get("setsukan_status") != "no_prior_meeting_runs"
        or setsukan.get("setsukan_status") != "no_prior_meeting_runs"
        or racer.get("season_runs")
        or racer.get("season_groups")
        or setsukan.get("season_runs")
        or setsukan.get("season_groups")
        or racer.get("setsukan_first_entry_date") != target_date
        or setsukan.get("setsukan_first_entry_date") != target_date
    ):
        return False
    evidence = racer.get("setsukan_evidence")
    if not isinstance(evidence, dict) or evidence != setsukan.get("setsukan_evidence"):
        return False
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    expected_days = list(range(1, event_day))
    expected_dates = [
        (target - timedelta(days=event_day - prior_day)).isoformat()
        for prior_day in expected_days
    ]
    if (
        evidence.get("source") != "published_prior_meeting_data"
        or evidence.get("venue") != slug
        or evidence.get("checked_dates") != expected_dates
        or evidence.get("checked_event_days") != expected_days
        or evidence.get("prior_race_appearances") != 0
    ):
        return False
    name = normalize_racer_name(racer.get("name"))
    if not name:
        return False
    for prior_day, prior_date in zip(expected_days, expected_dates):
        prior_path = after_root / "venues" / slug / f"{prior_date.replace('-', '')}.json"
        if not prior_path.is_file():
            return False
        try:
            prior = load_json(prior_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return False
        races = prior.get("races")
        if (
            prior.get("date") != prior_date
            or prior.get("venueId") != slug
            or prior.get("eventDay") != prior_day
            or not isinstance(races, list)
            or len(races) != 12
            or any(len(race.get("racers") or []) != 6 for race in races)
        ):
            return False
        if any(
            normalize_racer_name(previous.get("name")) == name
            for race in races
            for previous in race.get("racers") or []
        ):
            return False
    return True


def compare_independent_race_domains(slug: str, before: dict, after: dict, errors: list[str]) -> None:
    before_races = {str(race.get("race")): race for race in before.get("races") or []}
    after_races = {str(race.get("race")): race for race in after.get("races") or []}
    for race_no, previous in before_races.items():
        current = after_races.get(race_no) or {}
        for key in ("live", "odds", "result"):
            if has_value(previous.get(key)) and not has_value(current.get(key)):
                errors.append(f"{slug} {race_no}R: independent {key} disappeared")
        previous_setsukan = previous.get("setsukan")
        if has_value(previous_setsukan) and not has_value(current.get("setsukan")):
            errors.append(f"{slug} {race_no}R: independent setsukan disappeared")


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
        if previous.get("prediction_history") != current.get("prediction_history"):
            errors.append(f"{slug} {race_no}R: prediction history changed")
        if previous.get("active_prediction_stage") != current.get("active_prediction_stage"):
            errors.append(f"{slug} {race_no}R: active prediction stage changed")


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
    before_venue_files = {
        path.relative_to(before_root)
        for path in (before_root / "venues").rglob("*.json")
    } if (before_root / "venues").is_dir() else set()
    after_venue_files = {
        path.relative_to(after_root)
        for path in (after_root / "venues").rglob("*.json")
    } if (after_root / "venues").is_dir() else set()
    missing_venue_files = sorted(str(path) for path in before_venue_files - after_venue_files)
    if missing_venue_files:
        errors.append(f"venue JSON files disappeared: {missing_venue_files[:10]}")

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
        if before.get("date") != after.get("date"):
            before = {}
        races = after.get("races") or []
        if (
            len(races) != 12
            or any(
                len(race.get("racers") or []) != 6
                or sorted(int(racer.get("lane") or 0) for racer in race.get("racers") or [])
                != list(range(1, 7))
                or not race.get("deadline")
                for race in races
            )
        ):
            errors.append(f"{slug}: published race data is incomplete")
        if after.get("engine") == "deterministic_baseline_v1":
            errors.append(f"{slug}: engine changed to deterministic_baseline_v1")

        if slug not in PREDICTION_VENUES:
            if venue.get("predictionStatus") != "unavailable":
                errors.append(f"{slug}: unregistered venue is not marked unavailable")
            continue

        if before:
            compare_prediction_domains(slug, before, after, errors)
            compare_independent_race_domains(slug, before, after, errors)
        if venue.get("predictionStatus") != "ready":
            if venue.get("predictionStatus") == "unavailable" and after.get("preds"):
                # Retained same-day predictions may remain stored for protection,
                # but the manifest keeps them hidden until the strict gate passes.
                pass
            continue
        day = after.get("eventDay")
        if isinstance(day, int) and day > 0:
            prediction_days.append(day)
        if not after.get("preds"):
            errors.append(f"{slug}: prediction is empty")
        for race in after.get("races") or []:
            race_no = race.get("race")
            racers = {
                int(racer.get("lane") or 0): racer
                for racer in race.get("racers") or []
            }
            setsukan_rows = race.get("setsukan")
            if not isinstance(setsukan_rows, list):
                errors.append(f"{slug} {race_no}R: setsukan lanes invalid")
                continue
            setsukan = {
                int(row.get("lane") or 0): row
                for row in setsukan_rows
                if isinstance(row, dict)
            }
            if (
                len(setsukan_rows) != 6
                or sorted(setsukan) != list(range(1, 7))
                or sorted(racers) != list(range(1, 7))
            ):
                errors.append(f"{slug} {race_no}R: setsukan lanes invalid")
                continue
            for lane in range(1, 7):
                racer = racers[lane]
                row = setsukan[lane]
                if (
                    row.get("season_runs") != (racer.get("season_runs") or [])
                    or row.get("season_groups") != (racer.get("season_groups") or [])
                ):
                    errors.append(f"{slug} {race_no}R lane {lane}: setsukan mismatch")
                    continue
                if isinstance(day, int) and day > 1 and not racer.get("season_runs"):
                    if not no_prior_meeting_runs_is_verified(
                        after_root,
                        slug,
                        after.get("date", ""),
                        day,
                        racer,
                        row,
                    ):
                        print(f"WARNING: {slug} {race_no}R: setsukan missing lanes")
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
