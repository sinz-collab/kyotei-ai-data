#!/usr/bin/env python3
"""Write an identity-valid failure status when a VPS fetch cannot start/finish."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--if-missing", action="store_true")
    parser.add_argument(
        "--config", type=Path, default=Path(__file__).with_name("venues.json")
    )
    args = parser.parse_args()

    parsed = date.fromisoformat(args.date)
    if parsed.isoformat() != args.date:
        raise RuntimeError(f"invalid target date: {args.date}")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    venues = {item["slug"]: item["name"] for item in config["venues"]}
    if args.venue not in venues:
        raise RuntimeError(f"unknown venue: {args.venue}")

    output_dir = args.root / venues[args.venue] / args.date.replace("-", "")
    status_path = output_dir / "fetch_status.json"
    if args.if_missing and status_path.is_file():
        return 0
    output_dir.mkdir(parents=True, exist_ok=True)
    status = {
        "slug": args.venue,
        "name": venues[args.venue],
        "date": args.date,
        "open": False,
        "entryCount": 0,
        "fetchAttempts": [],
        "precheck": {
            "open": False,
            "reason": args.reason,
            "source": "vps_morning_fetch_guard",
        },
        "failure": {
            "reason": args.reason,
            "source": "vps_morning_fetch_guard",
        },
    }
    status_path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
