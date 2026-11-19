#!/usr/bin/env python3
"""Repair display-only meeting labels in saved official race JSON and manifest."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from event_labels import event_metadata
from official_morning import atomic_json
from publish_official_stages import update_index_files

VENUES = ("kiryu", "toda", "heiwajima", "biwako", "fukuoka", "omura")


def repair(date: str, root: Path) -> dict:
    if (root / "manifest.json").is_file():
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("date") != date:
            raise RuntimeError("manifest_date_mismatch")
    updated = {}
    for slug in VENUES:
        path = root / "venues" / slug / f"{date.replace('-', '')}.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("date") != date or len(payload.get("races") or []) != 12:
            continue
        label, event_type = event_metadata(payload, root)
        if (payload.get("eventLabel") or "") == label and (payload.get("eventType") or "unknown") == event_type:
            continue
        payload["eventLabel"], payload["eventType"] = label, event_type
        atomic_json(path, payload)
        latest_path = path.parent / "latest.json"
        if latest_path.is_file():
            latest = json.loads(latest_path.read_text(encoding="utf-8"))
            if latest.get("date") == date:
                latest["eventLabel"], latest["eventType"] = label, event_type
                atomic_json(latest_path, latest)
        updated[slug] = payload
    if updated:
        update_index_files(root, date, updated)
    return {"date": date, "updated": {slug: row["eventLabel"] for slug, row in updated.items()}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    args = parser.parse_args()
    if args.date != datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat():
        raise SystemExit("Only JST today may be repaired")
    print(json.dumps(repair(args.date, args.data_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
