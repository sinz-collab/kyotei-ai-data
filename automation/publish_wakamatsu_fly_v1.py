from __future__ import annotations

import argparse
import json
from pathlib import Path

from apply_wakamatsu_v2 import atomic_write_json
from wakamatsu_fly_tickets_v1 import apply_wakamatsu_fly_v1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    compact = args.date.replace("-", "")
    venue_dir = Path(args.data_root) / "venues" / "wakamatsu"
    dated_path = venue_dir / f"{compact}.json"
    latest_path = venue_dir / "latest.json"
    if not dated_path.exists():
        raise FileNotFoundError(f"Wakamatsu JSON does not exist: {dated_path}")

    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    apply_wakamatsu_fly_v1(payload)
    atomic_write_json(dated_path, payload)

    latest = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else None
    if isinstance(latest, dict) and str(latest.get("date", "")).replace("-", "") == compact:
        atomic_write_json(latest_path, payload)

    complete = sum(
        1
        for race in payload.get("races") or []
        if (race.get("prediction") or {}).get("flyPrediction", {}).get("status") == "complete"
    )
    print(json.dumps({
        "date": args.date,
        "eventType": payload.get("eventType"),
        "completeRaceCount": complete,
        "datedPath": str(dated_path),
        "latestUpdated": isinstance(latest, dict)
        and str(latest.get("date", "")).replace("-", "") == compact,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
