#!/usr/bin/env python3
"""Run the existing Karatsu engine against an isolated official-card data root.

This wrapper does not change the legacy apply_karatsu_v1.py command contract.
The caller merges only pre-deadline missing predictions into published JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from apply_karatsu_v1 import apply_file


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()

    compact = args.date.replace("-", "")
    if len(compact) != 8 or not compact.isdigit():
        parser.error("invalid date")
    target = args.data_root / "venues" / "karatsu" / f"{compact}.json"
    if not target.is_file():
        raise FileNotFoundError(target)
    apply_file(target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    print(json.dumps({
        "venue": "karatsu", "date": args.date,
        "raceCount": len(payload.get("preds") or {}),
        "engine": payload.get("engine"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
