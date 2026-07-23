from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from live_common import ROOT, load_json


def detect_active_venues(
    manifest_path: Path,
    today: str,
    data_root: Path | None = None,
) -> list[dict[str, Any]]:
    manifest = load_json(manifest_path)
    if manifest.get("date") != today:
        raise ValueError(f"manifest date mismatch: expected={today} actual={manifest.get('date')}")
    root = data_root or manifest_path.parent
    active = []
    for venue in manifest.get("venues", []):
        if not venue.get("open"):
            continue
        if venue.get("date") != today or int(venue.get("entryCount") or 0) != 12:
            continue
        data_path = str(venue.get("dataPath") or "")
        if not data_path:
            continue
        payload_path = root / data_path
        if not payload_path.is_file():
            continue
        payload = load_json(payload_path)
        races = payload.get("races")
        if payload.get("date") != today or not isinstance(races, list) or len(races) != 12:
            continue
        if any(len(race.get("racers") or []) != 6 for race in races):
            continue
        if any(not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", str(race.get("deadline") or "")) for race in races):
            continue
        active.append({"slug": venue["slug"], "name": venue["name"], "payload": payload, "manifest": venue})
    return active


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "data" / "manifest.json")
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    print(json.dumps(detect_active_venues(args.manifest, args.date), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
