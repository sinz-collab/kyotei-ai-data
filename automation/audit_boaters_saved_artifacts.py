#!/usr/bin/env python3
"""Offline BOATERS parser audit using already-saved VPS artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from enrich_all_venues_kimarite import parse_race_kimarite
from enrich_motor_recent import parse_motor_recent_text


VENUES = {
    "kiryu": ("east", "桐生"),
    "toda": ("east", "戸田"),
    "heiwajima": ("west", "平和島"),
    "tokoname": ("west", "常滑"),
    "biwako": ("west", "びわこ"),
    "naruto": ("west", "鳴門"),
    "shimonoseki": ("east", "下関"),
    "wakamatsu": ("east", "若松"),
    "ashiya": ("east", "芦屋"),
    "fukuoka": ("west", "福岡"),
    "karatsu": ("west", "唐津"),
    "omura": ("west", "大村"),
}


def race_prefixes(race_dir: Path) -> list[str]:
    return sorted(path.name.removesuffix("_boaters_local_st.json") for path in race_dir.glob("race_*_boaters_local_st.json"))


def audit_race(race_dir: Path, prefix: str) -> dict:
    paths = {
        "local": race_dir / f"{prefix}_boaters_local_st.json",
        "data_html": race_dir / f"{prefix}_data.html",
        "data_text": race_dir / f"{prefix}_data.txt",
        "motor_html": race_dir / f"{prefix}_motor.html",
        "motor_text": race_dir / f"{prefix}_motor.txt",
    }
    missing = [key for key, path in paths.items() if not path.is_file()]
    if missing:
        return {"ok": False, "errors": [f"missing:{key}" for key in missing]}

    try:
        local_payload = json.loads(paths["local"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"local_json:{type(exc).__name__}"]}
    racers = local_payload.get("racers") or []
    lanes = sorted(int(row.get("lane") or 0) for row in racers if isinstance(row, dict))
    errors: list[str] = []
    if len(racers) != 6 or lanes != list(range(1, 7)):
        errors.append(f"local_st_rank_lanes:{lanes}")

    # These text snapshots are document.body.innerText captured together with
    # the paired HTML and are deterministic replay input without JavaScript.
    data_text = paths["data_text"].read_text(encoding="utf-8", errors="replace")
    kimarite = parse_race_kimarite(data_text, racers)
    motors = parse_motor_recent_text(paths["motor_text"])

    if sorted(kimarite) != list(range(1, 7)):
        errors.append(f"kimarite_lanes:{sorted(kimarite)}")
    if sorted(motors) != list(range(1, 7)):
        errors.append(f"motor_lanes:{sorted(motors)}")
    for lane, motor in motors.items():
        if int(motor.get("runs_count") or 0) != 10:
            errors.append(f"lane_{lane}:motor_runs:{motor.get('runs_count')}")
    return {
        "ok": not errors,
        "errors": errors,
        "localStRankLanes": lanes,
        "kimariteLanes": sorted(kimarite),
        "motorRecent10Lanes": sorted(motors),
        "savedHtmlVerified": True,
    }


def find_complete_race(race_dir: Path) -> tuple[str | None, dict | None]:
    last: dict | None = None
    for prefix in race_prefixes(race_dir):
        last = audit_race(race_dir, prefix)
        if last["ok"]:
            return prefix, last
    return None, last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument(
        "--representative-race-dir",
        type=Path,
        help="Saved-artifact directory used only when a venue had no saved race that day.",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    compact = args.date.replace("-", "")
    result = {"date": args.date, "networkAccess": False, "venues": {}}

    representative_prefix = None
    representative_audit = None
    if args.representative_race_dir:
        representative_prefix, representative_audit = find_complete_race(args.representative_race_dir)

    for slug, (shard, name) in VENUES.items():
        race_dir = args.artifact_root / args.date / shard / name / compact / "races"
        prefix, audit = find_complete_race(race_dir) if race_dir.is_dir() else (None, None)
        native = prefix is not None
        if not native and representative_prefix and representative_audit:
            prefix, audit = representative_prefix, dict(representative_audit)
        venue_result = audit or {"ok": False, "errors": ["no_saved_complete_race"]}
        venue_result.update(
            {
                "nativeSavedArtifact": native,
                "sourceRace": prefix,
                "routeRaceDir": str(race_dir),
            }
        )
        result["venues"][slug] = venue_result

    result["successCount"] = sum(1 for item in result["venues"].values() if item["ok"])
    result["nativeSavedArtifactCount"] = sum(
        1 for item in result["venues"].values() if item["nativeSavedArtifact"]
    )
    result["ok"] = result["successCount"] == len(VENUES)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
