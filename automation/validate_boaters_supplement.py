#!/usr/bin/env python3
"""Validate isolated BOATERS race supplements before caching or transfer."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from enrich_all_venues_kimarite import parse_race_kimarite
from enrich_motor_recent import parse_motor_recent_text


def inspect_race(race_dir: Path, race_no: int) -> dict:
    prefix = f"race_{race_no:02d}"
    fatal_errors: list[str] = []
    unresolved: list[str] = []
    items: list[dict] = []
    local_path = race_dir / f"{prefix}_boaters_local_st.json"
    data_path = race_dir / f"{prefix}_data.txt"
    motor_path = race_dir / f"{prefix}_motor.txt"
    missing_files = [path.name for path in (local_path, data_path, motor_path) if not path.is_file()]
    if missing_files:
        return {
            "status": "partial",
            "fatalErrors": [],
            "unresolved": [f"missing_file:{name}" for name in missing_files],
            "items": items,
        }
    try:
        payload = json.loads(local_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "status": "failed",
            "fatalErrors": [f"local_json:{type(exc).__name__}"],
            "unresolved": [],
            "items": [],
        }
    racers = payload.get("racers") or []
    lanes = sorted(int(row.get("lane") or 0) for row in racers if isinstance(row, dict))
    if len(racers) != 6 or lanes != list(range(1, 7)):
        fatal_errors.append(f"local_racer_count:{len(racers)}")
    player_ids = {
        int(row.get("lane") or 0): str(row.get("player_id") or "")
        for row in racers if isinstance(row, dict)
    }
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
                unresolved.append(f"lane_{lane}:partial_source_missing")
            for item in ("localAvgSt", "localStRank"):
                items.append({
                    "lane": lane,
                    "playerId": str(row.get("player_id") or ""),
                    "item": item,
                    "status": "source_missing",
                    "reason": "source_dash",
                })
            continue
        if not re.fullmatch(r"0?\.\d{2}", avg):
            unresolved.append(f"lane_{lane}:local_st_invalid")
        if not re.fullmatch(r"\d+(?:\.\d+)?位", rank):
            unresolved.append(f"lane_{lane}:local_rank_invalid")
        items.extend([
            {"lane": lane, "playerId": str(row.get("player_id") or ""),
             "item": "localAvgSt", "status": "available" if re.fullmatch(r"0?\.\d{2}", avg) else "missing",
             "reason": "" if re.fullmatch(r"0?\.\d{2}", avg) else "invalid_or_unavailable"},
            {"lane": lane, "playerId": str(row.get("player_id") or ""),
             "item": "localStRank", "status": "available" if re.fullmatch(r"\d+(?:\.\d+)?位", rank) else "missing",
             "reason": "" if re.fullmatch(r"\d+(?:\.\d+)?位", rank) else "invalid_or_unavailable"},
        ])
    motors = parse_motor_recent_text(motor_path)
    if sorted(motors) != list(range(1, 7)):
        unresolved.append(f"motor_racer_count:{len(motors)}")
    for lane in range(1, 7):
        motor = motors.get(lane)
        runs = int((motor or {}).get("runs_count") or 0)
        status = "available" if 1 <= runs <= 10 else "missing"
        if status == "missing":
            unresolved.append(f"lane_{lane}:motor_runs:{runs}")
        items.append({
            "lane": lane, "playerId": player_ids.get(lane, ""), "item": "motorRecent",
            "status": status,
            "reason": "" if runs == 10 else (
                "source_has_fewer_than_10_runs" if 1 <= runs < 10 else "unavailable"
            ),
            "runsCount": runs,
        })
    kimarite = parse_race_kimarite(
        data_path.read_text(encoding="utf-8", errors="replace"), racers
    )
    if sorted(kimarite) != list(range(1, 7)):
        unresolved.append(f"kimarite_racer_count:{len(kimarite)}")
    for lane in range(1, 7):
        row = kimarite.get(lane)
        items.append({
            "lane": lane, "playerId": player_ids.get(lane, ""), "item": "kimarite",
            "status": "available" if row else "missing",
            "reason": "" if row else "unavailable",
        })
    return {
        "status": "failed" if fatal_errors else ("partial" if unresolved else "ok"),
        "fatalErrors": fatal_errors,
        "unresolved": unresolved,
        "items": items,
    }


def validate_race(race_dir: Path, race_no: int) -> list[str]:
    """Compatibility API: only race-fatal errors reject the whole archive."""
    return inspect_race(race_dir, race_no)["fatalErrors"]


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
        detail = inspect_race(args.race_dir, race_no)
        report["races"][str(race_no)] = detail
        if detail["fatalErrors"]:
            report["status"] = "failed"
        elif detail["status"] == "partial" and report["status"] == "ok":
            report["status"] = "partial"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] == "failed":
        return 1
    return 2 if report["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
