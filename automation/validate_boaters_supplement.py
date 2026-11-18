#!/usr/bin/env python3
"""Validate isolated BOATERS race supplements before caching or transfer."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from enrich_all_venues_kimarite import parse_race_kimarite
from enrich_motor_recent import parse_motor_recent_text


def validate_race(race_dir: Path, race_no: int) -> list[str]:
    prefix = f"race_{race_no:02d}"
    errors: list[str] = []
    local_path = race_dir / f"{prefix}_boaters_local_st.json"
    data_path = race_dir / f"{prefix}_data.txt"
    motor_path = race_dir / f"{prefix}_motor.txt"
    for path in (local_path, data_path, motor_path):
        if not path.is_file():
            errors.append(f"missing_file:{path.name}")
    if errors:
        return errors
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"local_json:{type(exc).__name__}"]
    racers = payload.get("racers") or []
    lanes = sorted(int(row.get("lane") or 0) for row in racers if isinstance(row, dict))
    if len(racers) != 6 or lanes != list(range(1, 7)):
        errors.append(f"local_racer_count:{len(racers)}")
    for row in racers:
        lane = int(row.get("lane") or 0)
        avg = str(row.get("boaters_local_avg_st") or "").strip()
        rank = str(row.get("boaters_local_st_rank") or "").strip()
        source_missing = (
            row.get("boaters_local_st_status") == "source_missing"
            or row.get("boaters_local_st_source_value") == "-"
            or (not avg and not rank)
        )
        if source_missing:
            if avg or rank:
                errors.append(f"lane_{lane}:partial_source_missing")
            continue
        if not re.fullmatch(r"0?\.\d{2}", avg):
            errors.append(f"lane_{lane}:local_st_invalid")
        if not re.fullmatch(r"\d+(?:\.\d+)?位", rank):
            errors.append(f"lane_{lane}:local_rank_invalid")
    motors = parse_motor_recent_text(motor_path)
    if sorted(motors) != list(range(1, 7)):
        errors.append(f"motor_racer_count:{len(motors)}")
    for lane, motor in motors.items():
        if int(motor.get("runs_count") or 0) != 10:
            errors.append(f"lane_{lane}:motor_runs:{motor.get('runs_count')}")
    kimarite = parse_race_kimarite(
        data_path.read_text(encoding="utf-8", errors="replace"), racers
    )
    if sorted(kimarite) != list(range(1, 7)):
        errors.append(f"kimarite_racer_count:{len(kimarite)}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--race-dir", required=True, type=Path)
    parser.add_argument("--races", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    races = sorted({int(value) for value in args.races.split(",") if value.strip()})
    if not races or any(value not in range(1, 13) for value in races):
        parser.error("races must be comma-separated values from 1 through 12")
    report = {
        "status": "ok",
        "raceDir": str(args.race_dir),
        "requestedRaces": races,
        "races": {},
    }
    for race_no in races:
        errors = validate_race(args.race_dir, race_no)
        report["races"][str(race_no)] = {"status": "ok" if not errors else "failed", "errors": errors}
        if errors:
            report["status"] = "failed"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
