#!/usr/bin/env python3
"""Assert that the dummy handoff reached the publication boundary safely."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads((args.data_root / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads(
        (args.data_root / "morning_report.json").read_text(encoding="utf-8")
    )
    if manifest.get("date") != args.date or report.get("date") != args.date:
        raise RuntimeError("dry-run date mismatch")
    report_venues = report.get("venues") or {}
    if len(report_venues) != 12:
        raise RuntimeError(
            f"expected 12 morning venues, got {len(report_venues)}"
        )
    manifest_venues = manifest.get("venues") or []
    if not set(report_venues) <= {item.get("slug") for item in manifest_venues}:
        raise RuntimeError("morning venues are missing from the public manifest")
    if any(item.get("predictionAvailable") for item in manifest_venues):
        raise RuntimeError("dummy data unexpectedly produced a publishable prediction")
    print("VPS artifact accepted; prediction build completed at the publish boundary.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
