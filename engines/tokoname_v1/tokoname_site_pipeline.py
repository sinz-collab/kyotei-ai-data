from __future__ import annotations

import argparse
import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable

if __package__:
    from .engine.predictor import predict
else:
    from engine.predictor import predict


ENGINE_NAME = "tokoname_engine"
ENGINE_VERSION = "1.6"
ENGINE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = ENGINE_DIR / "models"
LANES = tuple(range(1, 7))
LIVE_FILENAMES = (
    "direct.json",
    "exhibition.json",
    "original_exhibition.json",
)


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def validate_morning_document(document: dict, target_date: str) -> None:
    if document.get("venueId") != "tokoname" or document.get("venue") != "常滑":
        raise ValueError("not_tokoname_document")
    if document.get("date") != target_date:
        raise ValueError(
            f"date_mismatch: expected={target_date} actual={document.get('date')}"
        )
    races = document.get("races")
    if not isinstance(races, list) or len(races) != 12:
        raise ValueError("tokoname_races_must_be_12")
    if sorted(int(race.get("race") or 0) for race in races) != list(range(1, 13)):
        raise ValueError("tokoname_race_numbers_invalid")
    for race in races:
        racers = race.get("racers")
        if not isinstance(racers, list) or len(racers) != 6:
            raise ValueError(f"race_{int(race.get('race') or 0):02d}_racers_invalid")
        if sorted(int(racer.get("lane") or 0) for racer in racers) != list(LANES):
            raise ValueError(f"race_{int(race.get('race') or 0):02d}_lanes_invalid")


def validate_live_document(
    payload: dict,
    *,
    filename: str,
    target_date: str,
    race_no: int,
) -> None:
    if payload.get("venue") != "tokoname":
        raise ValueError(f"{filename}: venue_mismatch")
    if payload.get("date") != target_date:
        raise ValueError(f"{filename}: date_mismatch")
    if int(payload.get("race_no") or 0) != race_no:
        raise ValueError(f"{filename}: race_mismatch")
    if payload.get("complete") is not True or payload.get("status") != "complete":
        raise ValueError(f"{filename}: incomplete")
    if not isinstance(payload.get("data"), dict):
        raise ValueError(f"{filename}: data_missing")


def load_live_documents(live_race_dir: Path, target_date: str, race_no: int) -> dict:
    documents = {}
    for filename in LIVE_FILENAMES:
        path = live_race_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"live_file_missing: {path}")
        payload = load_json(path)
        validate_live_document(
            payload,
            filename=filename,
            target_date=target_date,
            race_no=race_no,
        )
        documents[filename.removesuffix(".json")] = payload
    return documents


def _integer(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed in LANES else None


def actual_course_map(direct: dict, exhibition: dict) -> dict[int, int]:
    data = direct["data"]
    actual_entry = data.get("actual_entry")
    entry_map: dict[int, int] = {}
    if actual_entry is not None:
        lanes = [_integer(value) for value in actual_entry]
        if len(lanes) != 6 or sorted(lanes) != list(LANES):
            raise ValueError(f"actual_entry_invalid: {actual_entry}")
        entry_map = {lane: course for course, lane in enumerate(lanes, 1)}

    explicit_map: dict[int, int] = {}
    actual_course = data.get("actual_course")
    if isinstance(actual_course, dict):
        explicit_map.update(
            {
                lane: course
                for raw_lane, raw_course in actual_course.items()
                if (lane := _integer(raw_lane)) is not None
                and (course := _integer(raw_course)) is not None
            }
        )
    elif isinstance(actual_course, list) and len(actual_course) == 6:
        explicit_map.update(
            {
                lane: course
                for lane, raw_course in enumerate(actual_course, 1)
                if (course := _integer(raw_course)) is not None
            }
        )

    for racer in data.get("racers") or []:
        lane = _integer(racer.get("lane"))
        course = _integer(racer.get("actual_course"))
        if lane is not None and course is not None:
            explicit_map[lane] = course

    exhibition_map = {}
    for entry in exhibition["data"].get("entries") or []:
        lane = _integer(entry.get("lane"))
        course = _integer(entry.get("exhibition_course"))
        if lane is not None and course is not None:
            exhibition_map[lane] = course

    merged = {}
    for lane in LANES:
        merged[lane] = explicit_map.get(lane) or entry_map.get(lane) or exhibition_map.get(lane) or lane

    if data.get("entry_changed") is True and sorted(merged.values()) != list(LANES):
        raise ValueError(f"actual_course_invalid_for_entry_change: {merged}")
    return merged


def build_engine_input(
    document: dict,
    race: dict,
    live_documents: dict,
) -> dict:
    direct = deepcopy(live_documents["direct"])
    exhibition = deepcopy(live_documents["exhibition"])
    original_exhibition = deepcopy(live_documents["original_exhibition"])

    exhibition["data"]["entries"] = sorted(
        exhibition["data"].get("entries") or [],
        key=lambda item: int(item.get("lane") or 0),
    )
    original_exhibition["data"]["entries"] = sorted(
        original_exhibition["data"].get("entries") or [],
        key=lambda item: int(item.get("lane") or 0),
    )
    if len(exhibition["data"]["entries"]) != 6:
        raise ValueError("exhibition_entries_must_be_6")
    if len(original_exhibition["data"]["entries"]) != 6:
        raise ValueError("original_exhibition_entries_must_be_6")

    courses = actual_course_map(direct, exhibition)
    direct_racers = {
        int(item["lane"]): item
        for item in direct["data"].get("racers") or []
        if _integer(item.get("lane")) is not None
    }

    racers = []
    for source_racer in sorted(race["racers"], key=lambda item: int(item["lane"])):
        racer = deepcopy(source_racer)
        lane = int(racer["lane"])
        live_racer = direct_racers.get(lane, {})
        racer["actual_course"] = courses[lane]
        player_id = live_racer.get("player_id") or racer.get("player_id")
        if player_id is not None:
            racer["player_id"] = player_id
        racers.append(racer)

    return {
        "date": document["date"],
        "eventDay": race.get("eventDay") or document.get("eventDay"),
        "race": {
            "race": int(race["race"]),
            "deadline": race.get("deadline"),
            "setsukan": deepcopy(race.get("setsukan") or []),
            "day_no": race.get("eventDay") or document.get("eventDay"),
            "racers": racers,
        },
        "direct": direct,
        "exhibition": exhibition,
        "original_exhibition": original_exhibition,
        "tide": deepcopy(race.get("tide") or document.get("tide")),
    }


def normalized_probability_map(rows: list[dict], key: str) -> dict[str, float]:
    values = {}
    for row in rows:
        lane = int(row["lane"])
        value = float(row[key])
        if lane not in LANES or not math.isfinite(value) or value < 0.0:
            raise ValueError(f"invalid_probability: lane={lane} key={key} value={value}")
        values[str(lane)] = round(value, 1)
    if set(values) != {str(lane) for lane in LANES}:
        raise ValueError(f"probability_lanes_invalid: {key}")
    correction = round(100.0 - sum(values.values()), 1)
    largest = max(values, key=values.get)
    values[largest] = round(values[largest] + correction, 1)
    return values


def site_prediction(engine_output: dict) -> dict:
    tickets = deepcopy(engine_output.get("tickets") or {})
    prediction = {
        "status": "ready",
        "reason": None,
        "engine": ENGINE_NAME,
        "engine_version": ENGINE_VERSION,
        "probabilities": {
            "win": normalized_probability_map(engine_output["probabilities"], "win"),
            "second": normalized_probability_map(engine_output["probabilities"], "second"),
            "third": normalized_probability_map(engine_output["probabilities"], "third"),
        },
        "sab": deepcopy(engine_output.get("sab")),
        "tickets": tickets,
        "scenario": deepcopy(engine_output.get("scenario")),
        "data_flags": {
            **deepcopy(engine_output.get("data_flags") or {}),
            "odds_used_for_probability": False,
            "result_used_for_probability": False,
        },
    }
    validate_site_prediction(prediction)
    return prediction


def validate_site_prediction(prediction: dict) -> None:
    if prediction.get("engine") != ENGINE_NAME:
        raise ValueError("site_engine_invalid")
    if prediction.get("engine_version") != ENGINE_VERSION:
        raise ValueError("site_engine_version_invalid")
    for key in ("win", "second", "third"):
        values = (prediction.get("probabilities") or {}).get(key)
        if not isinstance(values, dict) or set(values) != {str(lane) for lane in LANES}:
            raise ValueError(f"site_probability_shape_invalid: {key}")
        if abs(sum(float(value) for value in values.values()) - 100.0) > 0.2:
            raise ValueError(f"site_probability_total_invalid: {key}")
    tickets = prediction.get("tickets") or {}
    expected = {"main": 6, "deviation": 2, "upset": 2}
    if {key: len(tickets.get(key) or []) for key in expected} != expected:
        raise ValueError("site_ticket_counts_invalid")
    combinations = [
        ticket.get("combination")
        for key in ("main", "deviation", "upset")
        for ticket in tickets[key]
    ]
    if len(combinations) != 10 or len(set(combinations)) != 10:
        raise ValueError("site_tickets_must_be_10_unique")
    if prediction["data_flags"].get("odds_used_for_probability") is not False:
        raise ValueError("odds_must_not_be_used")


def without_predictions(document: dict) -> dict:
    comparable = deepcopy(document)
    for race in comparable.get("races") or []:
        race.pop("prediction", None)
    return comparable


def apply_tokoname_predictions(
    document: dict,
    *,
    live_root: Path,
    race_numbers: Iterable[int] = range(1, 13),
    model_dir: Path = DEFAULT_MODEL_DIR,
    predictor: Callable[[dict, Path], dict] = predict,
) -> tuple[dict, list[dict]]:
    target_date = str(document.get("date") or "")
    validate_morning_document(document, target_date)
    selected = {int(race_no) for race_no in race_numbers}
    if not selected or not selected.issubset(set(range(1, 13))):
        raise ValueError(f"race_numbers_invalid: {sorted(selected)}")
    if not model_dir.is_dir():
        raise FileNotFoundError(f"model_dir_missing: {model_dir}")

    updated = deepcopy(document)
    reports = []
    for race in updated["races"]:
        race_no = int(race["race"])
        if race_no not in selected:
            continue
        had_prediction = "prediction" in race
        existing_prediction = deepcopy(race.get("prediction"))
        try:
            live_documents = load_live_documents(
                live_root / f"{race_no:02d}",
                target_date,
                race_no,
            )
            engine_input = build_engine_input(updated, race, live_documents)
            output = predictor(engine_input, model_dir)
            prediction = site_prediction(output)
            race["prediction"] = prediction
            reports.append(
                {
                    "race": race_no,
                    "status": "updated",
                    "entry_changed": bool(
                        live_documents["direct"]["data"].get("entry_changed")
                    ),
                    "actual_entry": deepcopy(
                        live_documents["direct"]["data"].get("actual_entry")
                    ),
                    "win_total": round(
                        sum(prediction["probabilities"]["win"].values()), 1
                    ),
                    "second_total": round(
                        sum(prediction["probabilities"]["second"].values()), 1
                    ),
                    "third_total": round(
                        sum(prediction["probabilities"]["third"].values()), 1
                    ),
                    "ticket_counts": {
                        key: len(prediction["tickets"][key])
                        for key in ("main", "deviation", "upset")
                    },
                    "head": max(
                        prediction["probabilities"]["win"],
                        key=prediction["probabilities"]["win"].get,
                    ),
                }
            )
        except Exception as exc:
            if had_prediction:
                race["prediction"] = existing_prediction
            else:
                race.pop("prediction", None)
            reports.append(
                {
                    "race": race_no,
                    "status": "preserved",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if without_predictions(updated) != without_predictions(document):
        raise RuntimeError("non_prediction_fields_changed")
    return updated, reports


def compare_results(
    reports: list[dict],
    updated: dict,
    live_root: Path,
) -> list[dict]:
    by_race = {int(race["race"]): race for race in updated["races"]}
    comparisons = []
    for report in reports:
        race_no = int(report["race"])
        if report["status"] != "updated":
            continue
        result_path = live_root / f"{race_no:02d}" / "result.json"
        if not result_path.is_file():
            comparisons.append({"race": race_no, "result": None, "ticket_hit": None})
            continue
        result = load_json(result_path)
        order = [str(value) for value in (result.get("data") or {}).get("order") or []]
        combination = "-".join(order[:3]) if len(order) >= 3 else None
        tickets = by_race[race_no]["prediction"]["tickets"]
        predicted = {
            ticket["combination"]
            for key in ("main", "deviation", "upset")
            for ticket in tickets[key]
        }
        comparisons.append(
            {
                "race": race_no,
                "result": combination,
                "ticket_hit": combination in predicted if combination else None,
            }
        )
    return comparisons


def parse_races(value: str) -> list[int]:
    races = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start, end = (int(item) for item in token.split("-", 1))
            races.update(range(start, end + 1))
        else:
            races.add(int(token))
    if not races or not races.issubset(set(range(1, 13))):
        raise argparse.ArgumentTypeError("races must be between 1 and 12")
    return sorted(races)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--races", type=parse_races, default=list(range(1, 13)))
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--compare-results", action="store_true")
    args = parser.parse_args()

    compact_date = args.date.replace("-", "")
    morning_path = args.data_root / "venues" / "tokoname" / f"{compact_date}.json"
    live_root = args.data_root / "live" / args.date / "tokoname"
    original_bytes = morning_path.read_bytes()
    document = json.loads(original_bytes.decode("utf-8"))
    if document.get("date") != args.date:
        raise ValueError(
            f"date_mismatch: expected={args.date} actual={document.get('date')}"
        )
    updated, reports = apply_tokoname_predictions(
        document,
        live_root=live_root,
        race_numbers=args.races,
    )
    comparisons = (
        compare_results(reports, updated, live_root)
        if args.compare_results
        else []
    )

    if args.write:
        if any(report["status"] != "updated" for report in reports):
            raise RuntimeError("write_aborted_because_prediction_was_preserved")
        atomic_write_json(morning_path, updated)
    elif morning_path.read_bytes() != original_bytes:
        raise RuntimeError("dry_run_modified_morning_json")

    print(
        json.dumps(
            {
                "mode": "write" if args.write else "dry-run",
                "date": args.date,
                "engine": ENGINE_NAME,
                "engine_version": ENGINE_VERSION,
                "morning_path": str(morning_path),
                "model_dir": str(DEFAULT_MODEL_DIR),
                "reports": reports,
                "result_comparisons": comparisons,
                "non_prediction_fields_changed": (
                    without_predictions(updated) != without_predictions(document)
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(report["status"] == "updated" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
