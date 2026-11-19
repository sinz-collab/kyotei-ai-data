#!/usr/bin/env python3
"""Keep official-first venues authoritative during the legacy VPS publication.

The legacy pipeline continues to publish other venues. For six official venues,
restore the same-day validated official snapshot from the immediately preceding
main checkout, including its LIVE/history state and top-page metadata.
"""

import argparse
import json
import shutil
from pathlib import Path

OFFICIAL_VENUES = ("kiryu", "toda", "heiwajima", "biwako", "fukuoka", "omura")
OFFICIAL_STATES = {"published_official", "published_supplement"}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def restore(before: Path, after: Path, date: str) -> list[str]:
    compact = date.replace("-", "")
    old_manifest = load(before / "manifest.json")
    new_manifest = load(after / "manifest.json")
    old_report = load(before / "morning_report.json")
    new_report = load(after / "morning_report.json")
    old_rows = {v.get("slug"): v for v in old_manifest.get("venues", [])}
    new_rows = {v.get("slug"): v for v in new_manifest.get("venues", [])}
    protected = []
    for venue in OFFICIAL_VENUES:
        old_path = before / "venues" / venue / f"{compact}.json"
        old = load(old_path)
        if old.get("date") != date or (
            (old.get("sourceStatus") or {}).get("official_morning") or {}
        ).get("status") not in OFFICIAL_STATES:
            continue
        target = after / "venues" / venue / f"{compact}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old_path, target)
        old_latest = before / "venues" / venue / "latest.json"
        if load(old_latest).get("date") == date:
            shutil.copy2(old_latest, target.parent / "latest.json")
        if old_manifest.get("date") == date and new_manifest.get("date") == date:
            if venue in old_rows and venue in new_rows:
                new_rows[venue].clear()
                new_rows[venue].update(old_rows[venue])
        if old_report.get("date") == date and new_report.get("date") == date:
            if venue in old_report.get("venues", {}):
                new_report.setdefault("venues", {})[venue] = old_report["venues"][venue]
        protected.append(venue)
    if protected:
        if old_manifest.get("date") == new_manifest.get("date") == date:
            (after / "manifest.json").write_text(
                json.dumps(new_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        if old_report.get("date") == new_report.get("date") == date:
            (after / "morning_report.json").write_text(
                json.dumps(new_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    return protected


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({"official_preserved": restore(args.before, args.after, args.date)}))
