from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.tokoname_v1 import tokoname_site_pipeline  # noqa: E402


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required: {path}")
    return payload


def tokoname_availability(data_root: Path, expected_date: str) -> tuple[str, str]:
    report_path = data_root / "morning_report.json"
    if not report_path.is_file():
        return "unknown", "availability_metadata_missing"

    report = load_json(report_path)
    if report.get("date") != expected_date:
        return "unknown", "availability_date_mismatch"

    venue = (report.get("venues") or {}).get("tokoname")
    if not isinstance(venue, dict):
        return "unknown", "availability_venue_missing"

    reason = str((venue.get("detail") or {}).get("reason") or "")
    if venue.get("open") or venue.get("raceDataAvailable"):
        return "open", reason or "race_data_available"
    if reason == "not_scheduled":
        return "not_open", reason
    return "fetch_failed", reason or "fetch_incomplete"


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    args, _ = parser.parse_known_args()

    compact_date = args.date.replace("-", "")
    morning_path = args.data_root / "venues" / "tokoname" / f"{compact_date}.json"
    if morning_path.is_file():
        return tokoname_site_pipeline.main()

    availability, reason = tokoname_availability(args.data_root, args.date)
    if availability == "not_open":
        print(
            json.dumps(
                {
                    "status": "not_running",
                    "availability": availability,
                    "reason": reason,
                    "morning_path": str(morning_path),
                },
                ensure_ascii=False,
            )
        )
        return 0

    raise FileNotFoundError(
        f"Tokoname morning data missing: {morning_path} "
        f"(availability={availability}, reason={reason})"
    )


if __name__ == "__main__":
    raise SystemExit(main())
