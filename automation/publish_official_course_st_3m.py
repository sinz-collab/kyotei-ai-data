#!/usr/bin/env python3
"""Backfill today's public racers with official course ST without predictions edits."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date
from pathlib import Path

try:
    from .course_st_3m import CourseSt3m
except ImportError:
    from course_st_3m import CourseSt3m


def enrich(payload: dict, model: CourseSt3m) -> tuple[dict, int]:
    import copy
    enriched = copy.deepcopy(payload)
    count = 0
    for race in enriched.get("races") or []:
        for racer in race.get("racers") or []:
            reg = racer.get("player_id") or racer.get("reg_no")
            course = (racer.get("actual_course") or racer.get("entry_course")
                      or racer.get("course") or racer.get("lane"))
            try:
                value = model.select(reg, int(course)) if reg and course else None
            except (ValueError, TypeError):
                value = None
            racer["official_course_st_3m"] = value or {
                "status": "unavailable", "average_st": None,
                "source": "boatrace_official_result_archive",
                "reason": "racer_or_course_missing",
            }
            count += bool(value and value["average_st"] is not None)
    return enriched, count


def atomic_json(path: Path, payload: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target_date = date.fromisoformat(args.date)
    model = CourseSt3m(args.db, target_date)
    if not model.complete:
        raise SystemExit(f"Official ST unavailable: {model.reason}")
    if target_date != date.fromisoformat(os.environ.get("TARGET_DATE", args.date)):
        raise SystemExit("date mismatch")
    changed, available = 0, 0
    for directory in sorted((args.data_root / "venues").glob("*")):
        path = directory / f"{args.date.replace('-', '')}.json"
        if not path.is_file():
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        if original.get("date") != args.date or len(original.get("races") or []) != 12:
            continue
        updated, count = enrich(original, model)
        if updated == original:
            continue
        if args.apply:
            atomic_json(path, updated)
            latest = directory / "latest.json"
            if latest.is_file():
                snapshot = json.loads(latest.read_text(encoding="utf-8"))
                if snapshot.get("date") == args.date:
                    enriched_latest, _ = enrich(snapshot, model)
                    atomic_json(latest, enriched_latest)
        changed += 1
        available += count
    print(json.dumps({"date": args.date, "venues_changed": changed,
                      "values_available": available, "applied": args.apply}, ensure_ascii=False))


if __name__ == "__main__":
    main()
