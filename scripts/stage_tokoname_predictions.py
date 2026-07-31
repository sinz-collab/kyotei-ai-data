from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.tokoname_v1.tokoname_site_pipeline import (
    DEFAULT_MODEL_DIR,
    LIVE_FILENAMES,
    apply_tokoname_predictions,
    atomic_write_json,
    load_json,
    validate_live_document,
    validate_morning_document,
    without_predictions,
)
from live_data_hash import content_hash


def _race_index(document: dict) -> dict[int, dict]:
    return {
        int(race.get("race") or 0): race
        for race in document.get("races") or []
        if int(race.get("race") or 0) in range(1, 13)
    }


def overlay_existing_predictions(morning: dict, staged: dict | None) -> dict:
    candidate = deepcopy(morning)
    if (
        not isinstance(staged, dict)
        or staged.get("date") != morning.get("date")
        or staged.get("venueId") != "tokoname"
    ):
        return candidate
    staged_races = _race_index(staged)
    for race_no, race in _race_index(candidate).items():
        previous = staged_races.get(race_no) or {}
        if "prediction" in previous:
            race["prediction"] = deepcopy(previous["prediction"])
    return candidate


def live_inputs_complete(live_race_dir: Path, target_date: str, race_no: int) -> bool:
    try:
        for filename in LIVE_FILENAMES:
            path = live_race_dir / filename
            if not path.is_file():
                return False
            validate_live_document(
                load_json(path),
                filename=filename,
                target_date=target_date,
                race_no=race_no,
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    return True


def result_is_complete(live_race_dir: Path) -> bool:
    try:
        return load_json(live_race_dir / "result.json").get("complete") is True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def prediction_input_hash(
    morning: dict,
    race_no: int,
    live_race_dir: Path,
) -> str:
    race = deepcopy(_race_index(morning)[race_no])
    race.pop("prediction", None)
    return content_hash(
        {
            "date": morning.get("date"),
            "eventDay": race.get("eventDay") or morning.get("eventDay"),
            "race": race,
            "tide": race.get("tide") or morning.get("tide"),
            **{
                filename.removesuffix(".json"): load_json(live_race_dir / filename)
                for filename in LIVE_FILENAMES
            },
        }
    )


def _load_existing(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload


def stage_tokoname_predictions(
    target_date: str,
    *,
    morning_root: Path = ROOT / "runtime" / "morning",
    live_root: Path = ROOT / "data" / "live",
    output_root: Path = ROOT / "runtime" / "predictions",
    race_numbers: Iterable[int] = range(1, 13),
    model_dir: Path = DEFAULT_MODEL_DIR,
    predictor: Callable[[dict, Path], dict] | None = None,
) -> dict[str, Any]:
    compact_date = target_date.replace("-", "")
    morning_path = morning_root / "venues" / "tokoname" / f"{compact_date}.json"
    dated_path = output_root / "venues" / "tokoname" / f"{compact_date}.json"
    latest_path = output_root / "venues" / "tokoname" / "latest.json"
    venue_live_root = live_root / target_date / "tokoname"

    morning = load_json(morning_path)
    validate_morning_document(morning, target_date)

    requested = sorted({int(race_no) for race_no in race_numbers})
    if not requested or not set(requested).issubset(set(range(1, 13))):
        raise ValueError(f"race_numbers_invalid: {requested}")
    ready = [
        race_no
        for race_no in requested
        if live_inputs_complete(venue_live_root / f"{race_no:02d}", target_date, race_no)
    ]
    if not ready:
        return {
            "status": "not_ready",
            "date": target_date,
            "requested_races": requested,
            "ready_races": [],
            "updated_races": [],
            "preserved_races": [],
            "dated_path": str(dated_path),
            "latest_path": str(latest_path),
            "written": False,
        }

    existing = _load_existing(dated_path) or _load_existing(latest_path)
    base = overlay_existing_predictions(morning, existing)
    base_races = _race_index(base)
    input_hashes = {}
    unchanged_races = []
    result_complete_races = []
    to_update = []
    for race_no in ready:
        live_race_dir = venue_live_root / f"{race_no:02d}"
        if result_is_complete(live_race_dir):
            result_complete_races.append(race_no)
            continue
        current_hash = prediction_input_hash(morning, race_no, live_race_dir)
        input_hashes[race_no] = current_hash
        previous = base_races[race_no].get("prediction")
        if (
            isinstance(previous, dict)
            and previous.get("status") == "ready"
            and previous.get("input_hash") == current_hash
        ):
            unchanged_races.append(race_no)
            continue
        to_update.append(race_no)

    if not to_update:
        return {
            "status": "unchanged",
            "date": target_date,
            "requested_races": requested,
            "ready_races": ready,
            "updated_races": [],
            "preserved_races": [],
            "unchanged_races": unchanged_races,
            "result_complete_races": result_complete_races,
            "dated_path": str(dated_path),
            "latest_path": str(latest_path),
            "written": False,
        }

    updated, reports = apply_tokoname_predictions(
        base,
        live_root=venue_live_root,
        race_numbers=to_update,
        model_dir=model_dir,
        predictor=predictor,
    )
    updated_by_race = _race_index(updated)
    for report in reports:
        race_no = int(report["race"])
        if report["status"] == "updated":
            updated_by_race[race_no]["prediction"]["input_hash"] = input_hashes[race_no]
    if without_predictions(updated) != without_predictions(morning):
        raise RuntimeError("non_prediction_fields_changed_from_morning")

    updated_races = [
        int(report["race"]) for report in reports if report["status"] == "updated"
    ]
    preserved_races = [
        int(report["race"]) for report in reports if report["status"] == "preserved"
    ]
    if not updated_races:
        return {
            "status": "preserved",
            "date": target_date,
            "requested_races": requested,
            "ready_races": ready,
            "updated_races": [],
            "preserved_races": preserved_races,
            "unchanged_races": unchanged_races,
            "result_complete_races": result_complete_races,
            "dated_path": str(dated_path),
            "latest_path": str(latest_path),
            "written": False,
        }

    if (
        _load_existing(dated_path) == updated
        and _load_existing(latest_path) == updated
    ):
        return {
            "status": "unchanged",
            "date": target_date,
            "requested_races": requested,
            "ready_races": ready,
            "updated_races": updated_races,
            "preserved_races": preserved_races,
            "unchanged_races": unchanged_races,
            "result_complete_races": result_complete_races,
            "dated_path": str(dated_path),
            "latest_path": str(latest_path),
            "written": False,
        }

    dated_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dated_path, updated)
    atomic_write_json(latest_path, updated)
    if dated_path.read_bytes() != latest_path.read_bytes():
        raise RuntimeError("dated_latest_mismatch")
    return {
        "status": "updated",
        "date": target_date,
        "requested_races": requested,
        "ready_races": ready,
        "updated_races": updated_races,
        "preserved_races": preserved_races,
        "unchanged_races": unchanged_races,
        "result_complete_races": result_complete_races,
        "dated_path": str(dated_path),
        "latest_path": str(latest_path),
        "written": True,
    }


def parse_races(value: str) -> list[int]:
    races = {int(token.strip()) for token in value.split(",") if token.strip()}
    if not races or not races.issubset(set(range(1, 13))):
        raise argparse.ArgumentTypeError("races must be comma-separated values from 1 to 12")
    return sorted(races)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage Tokoname predictions without modifying runtime/morning or data"
    )
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--morning-root", type=Path, default=ROOT / "runtime" / "morning")
    parser.add_argument("--live-root", type=Path, default=ROOT / "data" / "live")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "runtime" / "predictions",
    )
    parser.add_argument("--races", type=parse_races, default=list(range(1, 13)))
    args = parser.parse_args()
    report = stage_tokoname_predictions(
        args.date,
        morning_root=args.morning_root,
        live_root=args.live_root,
        output_root=args.output_root,
        race_numbers=args.races,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
