#!/usr/bin/env python3
"""Restore the existing nine-venue tide feed without altering predictions/LIVE.

Use the legacy fetch_one.fetch_tide implementation and apply only validated,
same-day tide documents to today's dated and latest public JSON.
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fetch_one import fetch_tide


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def has_tide(document: dict, date: str) -> bool:
    tide = document.get("tide") or {}
    return tide.get("date") == date and tide.get("status") == "ok" and bool(tide.get("events"))


def store(path: Path, document: dict) -> None:
    # Mirror the official publisher's atomic encoding; touch no other values.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def restore(date: str, data_root: Path, config: Path, cache_root: Path, fetcher=fetch_tide) -> dict:
    results = {}
    compact = date.replace("-", "")
    for venue in load(config).get("venues", []):
        slug = venue["slug"]
        if not venue.get("tide_url"):
            continue
        dated = data_root / "venues" / slug / f"{compact}.json"
        current = load(dated)
        if current.get("date") != date:
            results[slug] = "no_same_day_races"
            continue
        if has_tide(current, date):
            results[slug] = "already_available"
            continue
        cache = cache_root / slug
        cached = load(cache / "tide_today.json")
        if cached.get("date") != date or cached.get("status") != "ok" or not cached.get("events"):
            outcome = fetcher(venue, date, cache)
            cached = load(cache / "tide_today.json")
            if outcome.get("status") != "ok":
                results[slug] = f"fetch_failed:{outcome.get('reason', 'unknown')}"
                continue
        if cached.get("date") != date or cached.get("status") != "ok" or not cached.get("events"):
            results[slug] = "invalid_tide_cache"
            continue
        current["tide"] = cached
        store(dated, current)
        latest = dated.parent / "latest.json"
        latest_document = load(latest)
        if latest_document.get("date") == date:
            latest_document["tide"] = cached
            store(latest, latest_document)
        results[slug] = "restored"
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("venues.json"))
    parser.add_argument("--cache-root", type=Path, default=Path("work/tide-recovery"))
    args = parser.parse_args()
    if args.date != datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat():
        raise SystemExit("Tide recovery may only fetch JST today")
    print(json.dumps(restore(args.date, args.data_root, args.config, args.cache_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
