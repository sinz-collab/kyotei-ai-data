#!/usr/bin/env python3
"""Select only today's missing official cards for safe fallback acquisition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

VENUES = ("kiryu", "toda", "heiwajima", "biwako", "fukuoka", "omura")


def valid_card(path: Path, date: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    races = payload.get("races") or []
    if not isinstance(races, list) or not all(isinstance(row, dict) for row in races):
        return False
    return (
        payload.get("date") == date
        and len(races) == 12
        and {int(race.get("race") or 0) for race in races} == set(range(1, 13))
        and all(len(race.get("racers") or []) == 6 and race.get("deadline") for race in races)
    )


def missing_venues(data_root: Path, date: str) -> list[str]:
    compact = date.replace("-", "")
    return [slug for slug in VENUES if not valid_card(data_root / "venues" / slug / f"{compact}.json", date)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    print(",".join(missing_venues(args.data_root, args.date)))


if __name__ == "__main__":
    main()
