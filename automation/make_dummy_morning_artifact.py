#!/usr/bin/env python3
"""Create a source-free morning artifact for the VPS handoff smoke test."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from prepare_morning_artifacts import SHARDS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--shard", choices=sorted(SHARDS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("venues.json")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    parsed_date = date.fromisoformat(args.date)
    if parsed_date.isoformat() != args.date:
        raise RuntimeError(f"invalid target date: {args.date}")
    if args.output.exists() and any(args.output.iterdir()):
        raise RuntimeError(f"output directory must be empty: {args.output}")

    config = json.loads(args.config.read_text(encoding="utf-8"))
    venues = {item["slug"]: item["name"] for item in config["venues"]}
    date_dir = args.date.replace("-", "")
    for slug in SHARDS[args.shard]:
        venue_name = venues[slug]
        destination = args.output / "work" / "races" / venue_name / date_dir
        destination.mkdir(parents=True, exist_ok=True)
        status = {
            "slug": slug,
            "name": venue_name,
            "date": args.date,
            "open": False,
            "entryCount": 0,
            "fetchAttempts": [],
            "precheck": {
                "open": False,
                "reason": "vps_handoff_dummy",
                "source": "synthetic-no-network",
            },
            "handoffTest": True,
            "shard": args.shard,
        }
        (destination / "fetch_status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(
        f"Created {args.shard} dummy artifact for {len(SHARDS[args.shard])} venues."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
