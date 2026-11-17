#!/usr/bin/env python3
"""Quarantine complete-looking VPS venues that fail publication validation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_site_data import build_payload
from public_json import serialize_public_json


REQUIRED_RACER_FIELDS = {
    "lane", "name", "class", "age", "weight", "branch", "avg_st",
    "nat_win", "nat_2", "nat_3", "local_win", "local_2", "local_3",
    "motor_no", "motor_2", "motor_3", "boat_no", "boat_2", "boat_3",
    "player_id",
}


def payload_error(payload: dict | None, detail: dict) -> str:
    if payload is None or detail.get("reason") != "ok":
        return f"public_json:{detail.get('reason', 'unknown')}"
    races = payload.get("races") or []
    if len(races) != 12:
        return f"race_count:{len(races)}"
    for race in races:
        racers = race.get("racers") or []
        if len(racers) != 6:
            return f"race_{race.get('race')}_racer_count:{len(racers)}"
        for racer in racers:
            missing = REQUIRED_RACER_FIELDS - racer.keys()
            if missing:
                return f"race_{race.get('race')}_missing:{','.join(sorted(missing))}"
    json.loads(serialize_public_json(payload))
    return ""


def quarantine(status_path: Path, status: dict, reason: str) -> None:
    status["open"] = False
    status["failure"] = {
        "reason": "artifact_validation_failed",
        "detail": reason,
        "source": "validate_morning_vps_artifacts",
    }
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("venues.json")
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    venues = {item["slug"]: item for item in config["venues"]}
    date_dir = args.date.replace("-", "")
    ready: list[str] = []
    quarantined: dict[str, str] = {}
    for slug, venue in venues.items():
        venue_dir = args.root / venue["name"] / date_dir
        status_path = venue_dir / "fetch_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("open") is not True or status.get("entryCount") != 12:
            continue
        payload, detail = build_payload(venue, args.date, venue_dir)
        reason = payload_error(payload, detail)
        if reason:
            quarantine(status_path, status, reason)
            quarantined[slug] = reason
        else:
            ready.append(slug)

    print(json.dumps({"ready": ready, "quarantined": quarantined}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
