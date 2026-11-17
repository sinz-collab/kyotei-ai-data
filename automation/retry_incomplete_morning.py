#!/usr/bin/env python3
"""Retry only scheduled venues whose restored morning fetch is incomplete."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
VENUE_ORDER = (
    "kiryu",
    "ashiya",
    "shimonoseki",
    "toda",
    "wakamatsu",
    "tokoname",
    "omura",
    "karatsu",
    "fukuoka",
    "heiwajima",
    "biwako",
    "naruto",
)


def retry_slugs(root: Path, target_date: str) -> list[str]:
    date_dir = target_date.replace("-", "")
    selected: list[str] = []
    for status_path in sorted(root.glob(f"*/{date_dir}/fetch_status.json")):
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if status.get("date") != target_date:
            continue
        complete = status.get("open") is True and status.get("entryCount") == 12
        scheduled = (status.get("precheck") or {}).get("open") is True
        if scheduled and not complete:
            selected.append(str(status["slug"]))
    order = {slug: index for index, slug in enumerate(VENUE_ORDER)}
    return sorted(selected, key=order.__getitem__)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--root", type=Path, default=Path("work/races"))
    args = parser.parse_args()

    venues = retry_slugs(args.root, args.date)
    print(f"Retry venues: {' '.join(venues) if venues else '(none)'}", flush=True)
    failed: list[str] = []
    for venue in venues:
        result = subprocess.run(
            [
                sys.executable,
                str(HERE / "fetch_one.py"),
                "--venue",
                venue,
                "--date",
                args.date,
                "--root",
                str(args.root),
            ],
            check=False,
        )
        if result.returncode != 0:
            failed.append(venue)

    if failed:
        print(f"Retry incomplete (will publish explicit gaps): {' '.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
