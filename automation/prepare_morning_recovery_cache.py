#!/usr/bin/env python3
"""Reuse validated VPS artifacts and select venues for manual recovery."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_site_data import build_payload
from validate_morning_vps_artifacts import payload_error


def load_venue(config: Path, slug: str) -> dict:
    payload = json.loads(config.read_text(encoding="utf-8"))
    for venue in payload.get("venues", []):
        if venue.get("slug") == slug:
            return venue
    raise RuntimeError(f"unknown venue: {slug}")


def load_status(source: Path) -> dict | None:
    path = source / "fetch_status.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def cache_is_complete(source: Path, venue: dict, target_date: str) -> bool:
    status = load_status(source)
    if not status or (
        status.get("slug") != venue["slug"]
        or status.get("name") != venue["name"]
        or status.get("date") != target_date
        or status.get("open") is not True
        or status.get("entryCount") != 12
    ):
        return False
    payload, detail = build_payload(venue, target_date, source)
    return payload is not None and not payload_error(payload, detail)


def is_failed_status(status: dict | None) -> bool:
    if status is None:
        return True
    if status.get("failure"):
        return True
    reason = str((status.get("precheck") or {}).get("reason") or "")
    return reason.startswith("precheck_failed:")


def copy_cached(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--venue", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--scope", choices=("all", "failed"), required=True)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("venues.json")
    )
    args = parser.parse_args()

    venue = load_venue(args.config, args.venue)
    date_dir = args.date.replace("-", "")
    source = args.cache_root / venue["name"] / date_dir
    destination = args.run_root / venue["name"] / date_dir
    status = load_status(source)

    if cache_is_complete(source, venue, args.date):
        copy_cached(source, destination)
        print(f"reuse_complete:{args.venue}")
        return 10

    if args.scope == "failed" and not is_failed_status(status):
        copy_cached(source, destination)
        print(f"skip_not_failed:{args.venue}")
        return 11

    copy_cached(source, destination)
    print(f"recover:{args.venue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
