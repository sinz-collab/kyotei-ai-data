#!/usr/bin/env python3
"""Publish validated official cards and BOATERS additions without running predictions."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from official_morning import (
    VENUES,
    atomic_json,
    protected_projection,
    merge_official_races,
    supplement_item_state,
    validate_candidate,
)
from event_labels import event_metadata


LOCAL_KEYS = {
    "local_st", "boaters_local_avg_st", "boaters_local_st_rank",
    "boaters_local_st_status", "boaters_local_st_missing_reason",
    "boaters_local_st_source_value", "boaters_local_st_rank_source_value",
}
KIMARITE_KEYS = {
    "boaters_kimarite_starts", "boaters_escape_rate", "boaters_sashare_rate",
    "boaters_makurare_rate", "boaters_makurare_zashi_rate", "boaters_nigashi_rate",
    "boaters_sashi_rate", "boaters_makuri_rate", "boaters_makuri_sashi_rate",
}


def load_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.json") if path.name != "report.json")


def candidate_slug(payload: dict[str, Any], path: Path) -> str:
    return str(payload.get("venueId") or payload.get("venue") or path.stem)


def prediction_state(payload: dict[str, Any]) -> tuple[bool, str, str]:
    status = str(payload.get("predictionStatus") or "")
    has_prediction = bool(payload.get("preds")) or any(
        bool(race.get("prediction") or race.get("predictions") or race.get("aiPrediction"))
        for race in payload.get("races") or []
    )
    if has_prediction or status == "ready":
        return True, status or "ready", str(payload.get("predictionReason") or "")
    return False, "supplement_pending", "supplemental_data_incomplete"


def race_map(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(row.get("race") or 0): row for row in payload.get("races") or []}


def racer_map(race: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {
        (str(row.get("player_id") or ""), int(row.get("lane") or 0)): row
        for row in race.get("racers") or []
    }


def valid_motor(value: Any) -> bool:
    return isinstance(value, dict) and 1 <= int(value.get("runs_count") or 0) <= 10


def merge_supplements(target: dict[str, Any], candidate: dict[str, Any]) -> int:
    """Merge four requested groups only; never replace a normal value with absence."""
    added = 0
    target_races = race_map(target)
    for race_no, source_race in race_map(candidate).items():
        target_race = target_races.get(race_no)
        if target_race is None:
            continue
        targets = racer_map(target_race)
        for source in source_race.get("racers") or []:
            key = (str(source.get("player_id") or ""), int(source.get("lane") or 0))
            dest = targets.get(key)
            if dest is None:
                continue

            local_available = bool(source.get("boaters_local_avg_st")) and bool(
                source.get("boaters_local_st_rank")
            )
            local_missing = source.get("boaters_local_st_status") == "source_missing"
            if local_available:
                before_avg = bool(dest.get("boaters_local_avg_st"))
                before_rank = bool(dest.get("boaters_local_st_rank"))
                for field in LOCAL_KEYS:
                    if field in source:
                        dest[field] = copy.deepcopy(source[field])
                added += int(not before_avg) + int(not before_rank)
            elif local_missing and not (
                dest.get("boaters_local_avg_st") or dest.get("boaters_local_st_rank")
            ):
                for field in LOCAL_KEYS:
                    if field in source:
                        dest[field] = copy.deepcopy(source[field])

            if valid_motor(source.get("motor_recent")):
                before = valid_motor(dest.get("motor_recent"))
                dest["motor_recent"] = copy.deepcopy(source["motor_recent"])
                added += int(not before)

            if source.get("boaters_kimarite_starts") is not None:
                before = dest.get("boaters_kimarite_starts") is not None
                for field in KIMARITE_KEYS:
                    if field in source:
                        dest[field] = copy.deepcopy(source[field])
                added += int(not before)

            source_provenance = source.get("supplement_sources") or {}
            if source_provenance:
                provenance = dest.setdefault("supplement_sources", {})
                for field in ("local_st", "motor_recent", "kimarite"):
                    if field in source_provenance:
                        provenance[field] = copy.deepcopy(source_provenance[field])
    return added


def source_status(
    payload: dict[str, Any], stage: str, *, state_payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    # A current source dash remains explicit even when a prior normal cached
    # value is retained to satisfy the no-downgrade rule.
    states = supplement_item_state(state_payload or payload)
    missing = sum(row["status"] == "missing" for row in states)
    available = sum(row["status"] == "available" for row in states)
    source_missing = sum(row["status"] == "source_missing" for row in states)
    return {
        "status": "published_supplement" if stage == "supplement" else "published_official",
        "source": "boatrace_official_racelist",
        "date": payload.get("date"),
        "races": list(range(1, 13)),
        "supplementComplete": missing == 0,
        "supplementCounts": {
            "available": available, "sourceMissing": source_missing, "missing": missing,
        },
        "supplementState": states,
    }


def reset_manifest_entry(entry: dict[str, Any], date: str) -> None:
    entry.update({
        "open": False, "entryCount": 0, "firstDeadline": "", "date": date,
        "dateDir": date.replace("-", ""), "dataPath": "", "latestPath": "",
        "raceDataAvailable": False, "race_data_available": False,
        "predictionAvailable": False, "prediction_available": False,
        "availabilityReason": "not_published", "predictionStatus": "not_running",
        "prediction_status": "not_running", "predictionReason": "",
        "prediction_reason": "", "deadlines": [],
    })


def update_manifest_entry(entry: dict[str, Any], payload: dict[str, Any], date: str) -> None:
    deadlines = [
        {"race": int(race["race"]), "deadline": race["deadline"]}
        for race in payload.get("races") or []
    ]
    pred_available, pred_status, pred_reason = prediction_state(payload)
    slug = str(payload.get("venueId") or payload.get("venue"))
    entry.update({
        "slug": slug, "venue": slug, "name": VENUES.get(slug, ("", slug))[1],
        "open": True, "entryCount": 12, "firstDeadline": deadlines[0]["deadline"],
        "date": date, "dateDir": date.replace("-", ""),
        "dataPath": f"venues/{slug}/{date.replace('-', '')}.json",
        "latestPath": f"venues/{slug}/latest.json", "raceDataAvailable": True,
        "race_data_available": True, "predictionAvailable": pred_available,
        "prediction_available": pred_available, "availabilityReason": "ok",
        "predictionStatus": pred_status, "prediction_status": pred_status,
        "predictionReason": pred_reason, "prediction_reason": pred_reason,
        "deadlines": deadlines, "eventDay": payload.get("eventDay"),
        "eventDayLabel": payload.get("eventDayLabel"),
        "eventLabel": payload.get("eventLabel") or "",
        "eventType": payload.get("eventType") or "unknown",
    })


def update_index_files(data_root: Path, date: str, published: dict[str, dict[str, Any]]) -> None:
    now = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    manifest_path = data_root / "manifest.json"
    manifest = load_json(manifest_path, {"version": 1, "venues": []})
    if manifest.get("date") != date:
        for entry in manifest.get("venues") or []:
            reset_manifest_entry(entry, date)
    manifest.update({"updatedAt": now, "date": date, "dateDir": date.replace("-", "")})
    entries = {str(row.get("slug") or row.get("venue")): row for row in manifest.get("venues") or []}
    for slug, payload in published.items():
        entry = entries.get(slug)
        if entry is None:
            entry = {"slug": slug, "venue": slug}
            manifest.setdefault("venues", []).append(entry)
        update_manifest_entry(entry, payload, date)
    atomic_json(manifest_path, manifest)

    report_path = data_root / "morning_report.json"
    report = load_json(report_path, {"venues": {}})
    if report.get("date") != date:
        report = {"date": date, "createdAt": now, "venues": {}}
    report["createdAt"] = now
    for slug, payload in published.items():
        available, status, reason = prediction_state(payload)
        deadlines = [{"race": int(r["race"]), "deadline": r["deadline"]} for r in payload["races"]]
        stage = copy.deepcopy(payload["sourceStatus"]["official_morning"])
        stage.pop("supplementState", None)
        report["venues"][slug] = {
            "open": True, "raceDataAvailable": True, "predictionAvailable": available,
            "predictionStatus": status, "predictionReason": reason, "entryCount": 12,
            "firstDeadline": deadlines[0]["deadline"], "deadlines": deadlines,
            "eventDay": payload.get("eventDay"), "eventDayLabel": payload.get("eventDayLabel"),
            "eventLabel": payload.get("eventLabel") or "",
            "eventType": payload.get("eventType") or "unknown",
            "eventTitle": payload.get("officialEventTitle") or "",
            "detail": {"reason": "ok", "officialStage": stage},
        }
    atomic_json(report_path, report)


def publish(args: argparse.Namespace) -> dict[str, Any]:
    published: dict[str, dict[str, Any]] = {}
    result: dict[str, Any] = {
        "date": args.date, "stage": args.stage, "publishedVenues": [],
        "publishedRaces": 0, "addedItems": 0, "skipped": [],
    }
    for path in candidate_files(args.candidate_root):
        candidate = load_json(path)
        slug = candidate_slug(candidate, path)
        errors = validate_candidate(None, candidate, requested_races=range(1, 13), require_supplements=False)
        if errors or len(candidate.get("races") or []) != 12:
            result["skipped"].append({"venue": slug, "reason": "official_incomplete", "errors": errors})
            continue
        venue_root = args.data_root / "venues" / slug
        dated_path = venue_root / f"{args.date.replace('-', '')}.json"
        existing = load_json(dated_path)
        if args.stage == "official":
            if existing is not None:
                # LIVE publication can advance after the candidate was built.
                # Merge official card fields into the newest saved payload so
                # predictions, LIVE data, results and supplements survive.
                payload = merge_official_races(
                    existing, candidate["races"], slug=slug, target_date=args.date
                )
                if protected_projection(existing) != protected_projection(payload):
                    raise RuntimeError(f"protected_domain_mismatch:{slug}")
            else:
                payload = copy.deepcopy(candidate)
        else:
            if existing is None or existing.get("date") != args.date:
                result["skipped"].append({"venue": slug, "reason": "official_stage_not_published"})
                continue
            payload = copy.deepcopy(existing)
            protected_before = protected_projection(payload)
            result["addedItems"] += merge_supplements(payload, candidate)
            if protected_projection(payload) != protected_before:
                raise RuntimeError(f"protected_domain_changed:{slug}")
        payload["eventLabel"], payload["eventType"] = event_metadata(payload, args.data_root)
        payload.setdefault("sourceStatus", {})["official_morning"] = source_status(
            payload, args.stage, state_payload=candidate if args.stage == "supplement" else None
        )
        pred_available, pred_status, pred_reason = prediction_state(payload)
        if not pred_available:
            payload["predictionStatus"] = pred_status
            payload["predictionReason"] = pred_reason
        atomic_json(dated_path, payload)
        latest_path = venue_root / "latest.json"
        latest = load_json(latest_path)
        if latest is None or str(latest.get("date") or "") <= args.date:
            atomic_json(latest_path, payload)
        published[slug] = payload
        result["publishedVenues"].append(slug)
        result["publishedRaces"] += 12
    update_index_files(args.data_root, args.date, published)
    result["publishedVenues"].sort()
    if args.report:
        atomic_json(args.report, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--stage", choices=("official", "supplement"), required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    print(json.dumps(publish(args), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
