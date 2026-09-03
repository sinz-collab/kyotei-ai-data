from __future__ import annotations

import argparse
import json
import math
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "fukuoka_v1"
ENGINE_ID = "fukuoka_engine_v1.0"
ENGINE_VERSION = "1.0"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from fukuoka_prediction_engine_v1_0 import FukuokaPredictionEngineV10  # noqa: E402


WIND_16_TO_8 = {
    1: "N", 2: "N", 3: "NE", 4: "NE",
    5: "E", 6: "E", 7: "SE", 8: "SE",
    9: "S", 10: "S", 11: "SW", 12: "SW",
    13: "W", 14: "W", 15: "NW", 16: "NW", 17: "CALM",
}


def number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-"):
        return default
    try:
        parsed = float(re.sub(r"[^0-9.\-]", "", str(value)))
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def integer(value: Any, default: int = 0) -> int:
    parsed = number(value)
    return default if parsed is None else int(parsed)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required: {path}")
    return value


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def complete_document(
    path: Path,
    target_date: str,
    race_no: int,
) -> dict | None:
    if not path.is_file():
        return None
    try:
        document = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (
        document.get("complete") is not True
        or document.get("status") != "complete"
        or document.get("date") != target_date
        or document.get("venue") != "fukuoka"
        or integer(document.get("race_no")) != race_no
        or not isinstance(document.get("data"), dict)
    ):
        return None
    return document


def live_documents(
    race_dir: Path,
    target_date: str,
    race_no: int,
) -> dict[str, dict] | None:
    documents = {
        name: complete_document(
            race_dir / f"{name}.json",
            target_date,
            race_no,
        )
        for name in ("direct", "exhibition", "original_exhibition")
    }
    if any(document is None for document in documents.values()):
        return None
    return {name: document for name, document in documents.items() if document is not None}


def indexed_entries(document: dict) -> dict[int, dict]:
    return {
        integer(entry.get("lane")): entry
        for entry in (document.get("data") or {}).get("entries") or []
        if isinstance(entry, dict) and integer(entry.get("lane")) in range(1, 7)
    }


def normalize_wind_direction(value: Any) -> str:
    code = integer(value)
    if code in WIND_16_TO_8:
        return WIND_16_TO_8[code]
    text = str(value or "").strip().upper()
    aliases = {
        "北": "N", "北東": "NE", "東": "E", "南東": "SE",
        "南": "S", "南西": "SW", "西": "W", "北西": "NW",
        "無風": "CALM",
    }
    return aliases.get(text, text)


def tide_phase(payload: dict, deadline: str) -> str | None:
    try:
        target = datetime.strptime(deadline, "%H:%M")
    except (TypeError, ValueError):
        return None
    events = []
    for event in (payload.get("tide") or {}).get("events") or []:
        try:
            when = datetime.strptime(str(event.get("time") or ""), "%H:%M")
        except (TypeError, ValueError):
            continue
        events.append((when, str(event.get("type") or "")))
    previous = [event for event in events if event[0] <= target]
    if not previous:
        return None
    event_type = max(previous, key=lambda item: item[0])[1]
    if "干潮" in event_type:
        return "rising"
    if "満潮" in event_type:
        return "falling"
    return None


def motor_trend(racer: dict) -> str:
    recent = racer.get("motor_recent") or {}
    value = str(recent.get("trend") or racer.get("motor_trend") or "flat").lower()
    return value if value in {"up", "down", "flat"} else "flat"


def build_engine_input(
    payload: dict,
    race: dict,
    documents: dict[str, dict] | None = None,
) -> dict:
    boats = []
    for racer in sorted(race.get("racers") or [], key=lambda row: integer(row.get("lane"))):
        lane = integer(racer.get("lane"))
        recent = racer.get("motor_recent") or {}
        boats.append(
            {
                "lane": lane,
                "actual_course": integer(
                    racer.get("actual_course") or racer.get("entry_course"),
                    lane,
                ),
                "entry_course": integer(racer.get("entry_course"), lane),
                "reg_no": racer.get("player_id") or racer.get("reg_no"),
                "name": racer.get("name") or "",
                "class": racer.get("class") or "",
                "national_win_rate": number(racer.get("nat_win"), 0.0),
                "local_win_rate": number(racer.get("local_win"), 0.0),
                "local_avg_st": number(
                    racer.get("boaters_local_avg_st")
                    or racer.get("local_st")
                    or racer.get("avg_st"),
                    0.18,
                ),
                "course_escape_rate": number(racer.get("boaters_escape_rate"), 0.0),
                "course_sashi_rate": number(racer.get("boaters_sashi_rate"), 0.0),
                "course_makuri_rate": number(racer.get("boaters_makuri_rate"), 0.0),
                "course_makuri_sashi_rate": number(
                    racer.get("boaters_makuri_sashi_rate")
                    or racer.get("boaters_makurizashi_rate"),
                    0.0,
                ),
                "motor_no": integer(racer.get("motor_no")),
                "motor_top2_rate": number(racer.get("motor_2")),
                "motor_top3_rate": number(racer.get("motor_3")),
                "motor_grade": racer.get("motor_grade") or "C",
                "motor_trend": motor_trend(racer),
                "motor_recent": deepcopy(recent),
                "setsukan_runs": deepcopy(racer.get("season_runs") or []),
            }
        )
    value = {
        "date": payload.get("date"),
        "race_no": integer(race.get("race")),
        "event_day": integer(race.get("eventDay") or payload.get("eventDay"), 1),
        "wind_direction": "",
        "wind_speed": None,
        "wave_height": None,
        "tide_phase": tide_phase(payload, str(race.get("deadline") or "")),
        "boats": boats,
    }
    if documents is not None:
        apply_live_input(value, documents)
    return value


def apply_live_input(race_input: dict, documents: dict[str, dict]) -> None:
    direct = documents["direct"]["data"]
    exhibition = indexed_entries(documents["exhibition"])
    original = indexed_entries(documents["original_exhibition"])
    direct_racers = {
        integer(row.get("lane")): row
        for row in direct.get("racers") or []
        if isinstance(row, dict)
    }
    actual_entry = [integer(lane) for lane in direct.get("actual_entry") or []]
    course_by_lane = {
        lane: course
        for course, lane in enumerate(actual_entry, 1)
        if lane in range(1, 7)
    }
    race_input["wind_direction_raw"] = direct.get("wind_direction")
    race_input["wind_direction"] = normalize_wind_direction(direct.get("wind_direction"))
    race_input["wind_speed"] = number(direct.get("wind_speed"), 0.0)
    race_input["wave_height"] = number(direct.get("wave_height"), 0.0)
    race_input["slit"] = deepcopy((documents["exhibition"].get("data") or {}).get("slit_source") or [])

    for boat in race_input["boats"]:
        lane = boat["lane"]
        ex = exhibition.get(lane) or {}
        oe = original.get(lane) or {}
        direct_racer = direct_racers.get(lane) or {}
        boat["actual_course"] = course_by_lane.get(
            lane,
            integer(ex.get("exhibition_course"), boat["entry_course"]),
        )
        if direct_racer.get("player_id") not in (None, ""):
            boat["reg_no"] = str(direct_racer["player_id"])
        boat["exhibition_time"] = number(ex.get("exhibition_time"))
        boat["exhibition_st"] = number(ex.get("start_time", ex.get("start_raw")))
        boat["original_lap"] = number(oe.get("lap_time"))
        boat["original_turn"] = number(oe.get("turn_time"))
        boat["original_straight"] = number(oe.get("straight_time"))
        boat["original_sum"] = number(oe.get("sum"))


def pct_map(result: dict, key: str) -> dict[str, float]:
    return {
        str(row["lane"]): round(float(row[key]) * 100.0, 2)
        for row in result["boats"]
    }


def ticket_rows(result: dict) -> tuple[list[dict], list[dict], list[dict]]:
    scored = {
        row["ticket"]: round(float(row.get("score") or 0.0) * 100.0, 4)
        for row in result["tickets"].get("ranked_top10") or []
    }

    def rows(key: str, role: str) -> list[dict]:
        return [
            {"combo": combo, "role": role, "prob": scored.get(combo, 0.0)}
            for combo in result["tickets"][key]
        ]

    return rows("main", "本線"), rows("deviation", "ずらし"), rows("upset", "穴")


def format_prediction(result: dict, phase: str, race_input: dict) -> dict:
    main, zure, ana = ticket_rows(result)
    is_final = phase == "final"
    win = pct_map(result, "win_prob")
    second = pct_map(result, "second_prob")
    third = pct_map(result, "third_prob")
    head_order = sorted(win, key=win.get, reverse=True)
    diagnostics = deepcopy(result.get("diagnostics") or {})
    diagnostics.update(
        {
            "oddsUsedForPrediction": False,
            "resultUsedForPrediction": False,
            "liveInputsComplete": is_final,
            "eventDay": race_input.get("event_day"),
            "tidePhase": race_input.get("tide_phase"),
            "windDirectionRaw": race_input.get("wind_direction_raw"),
        }
    )
    stage = {
        "label": "本予想" if is_final else "仮予想",
        "badge": "本予想" if is_final else "仮予想",
        "statusText": (
            "実進入・展示・スリット・オリジナル展示を反映して福岡v1.0で再予想済み"
            if is_final
            else "朝データを福岡v1.0へ反映。LIVE3点complete後に本予想へ更新"
        ),
        "color": "green" if is_final else "yellow",
    }
    return {
        "status": "complete",
        "engine": ENGINE_ID,
        "engineVersion": ENGINE_VERSION,
        "engine_version": ENGINE_VERSION,
        "phase": phase,
        "finalPredictionStatus": "complete" if is_final else "waiting_live_data",
        "predictionStage": stage,
        "win": win,
        "second": second,
        "third": third,
        "SAB": deepcopy(result["sab"]),
        "sab": result["sab"]["grade"],
        "sabDetail": deepcopy(result["sab"]),
        "Main6": [row["combo"] for row in main],
        "Zure2": [row["combo"] for row in zure],
        "Ana2": [row["combo"] for row in ana],
        "ai": main,
        "balance": zure,
        "aiUpset": ana,
        "tickets": main + zure + ana,
        "scenario": {
            "model": "conditional_trifecta",
            "formula": "P(1着) × P(2着|1着) × P(3着|1着,2着)",
            "headOrder": [int(lane) for lane in head_order],
        },
        "probabilityFlow": {
            "required": True,
            "baseApplied": True,
            "baseLabel": "福岡v1.0仮予想",
            "realtimeApplied": is_final,
            "realtimeLabel": "実進入・展示・スリット・オリジナル展示反映",
            "reviewed": is_final,
            "reviewLabel": "福岡v1.0本予想",
        },
        "diagnostics": diagnostics,
    }


def prediction_complete(prediction: Any, phase: str | None = None) -> bool:
    if not isinstance(prediction, dict) or prediction.get("engine") != ENGINE_ID:
        return False
    if phase is not None and prediction.get("phase") != phase:
        return False
    if any(not isinstance(prediction.get(key), dict) or len(prediction[key]) != 6 for key in ("win", "second", "third")):
        return False
    return len(prediction.get("tickets") or []) == 10


def sync_race_prediction(race: dict, prediction: dict, phase: str) -> None:
    if phase == "preliminary":
        race["predictionPre"] = deepcopy(prediction)
        race["predictionFinal"] = None
    else:
        race["predictionFinal"] = deepcopy(prediction)
    race["prediction"] = deepcopy(prediction)


def add_probability_review(final: dict, preliminary: dict) -> None:
    review = {}
    for lane in range(1, 7):
        key = str(lane)
        review[key] = {
            "morningWin": preliminary["win"][key],
            "morningSecond": preliminary["second"][key],
            "morningThird": preliminary["third"][key],
            "win": final["win"][key],
            "second": final["second"][key],
            "third": final["third"][key],
            "deltaWin": round(final["win"][key] - preliminary["win"][key], 2),
            "deltaSecond": round(final["second"][key] - preliminary["second"][key], 2),
            "deltaThird": round(final["third"][key] - preliminary["third"][key], 2),
        }
    final["probabilityReviewStatus"] = "reviewed"
    final["probabilityReview"] = review


def attach_live_domain(race: dict, documents: dict[str, dict]) -> None:
    direct = deepcopy(documents["direct"]["data"])
    exhibition = deepcopy(documents["exhibition"]["data"])
    original = deepcopy(documents["original_exhibition"]["data"])
    race["live"] = {
        "direct": direct,
        "weather": direct,
        "exhibition": exhibition,
        "original": original,
        "original_exhibition": original,
    }


def apply_predictions(
    payload: dict,
    target_date: str,
    phase: str,
    live_base: Path,
    race_filter: int | None = None,
) -> dict:
    if payload.get("date") != target_date or payload.get("venueId") != "fukuoka":
        raise RuntimeError("fukuoka_payload_identity_invalid")
    races = payload.get("races") or []
    if len(races) != 12 or sorted(integer(race.get("race")) for race in races) != list(range(1, 13)):
        raise RuntimeError("fukuoka_races_must_be_12")

    engine = FukuokaPredictionEngineV10()
    predictions = deepcopy(payload.get("preds") or {})
    updated = []
    skipped = []
    for race in races:
        race_no = integer(race.get("race"))
        if race_filter is not None and race_no != race_filter:
            continue
        current = race.get("prediction")
        if phase == "preliminary" and prediction_complete(current, "final"):
            predictions[str(race_no)] = deepcopy(current)
            skipped.append(race_no)
            continue
        documents = None
        if phase == "final":
            documents = live_documents(live_base / f"{race_no:02d}", target_date, race_no)
            if documents is None:
                if prediction_complete(current):
                    predictions[str(race_no)] = deepcopy(current)
                skipped.append(race_no)
                continue
        race_input = build_engine_input(payload, race, documents)
        prediction = format_prediction(engine.predict(race_input), phase, race_input)
        if phase == "final" and not prediction_complete(race.get("predictionPre"), "preliminary"):
            pre_input = build_engine_input(payload, race)
            race["predictionPre"] = format_prediction(engine.predict(pre_input), "preliminary", pre_input)
        if phase == "final":
            add_probability_review(prediction, race["predictionPre"])
        sync_race_prediction(race, prediction, phase)
        if documents is not None:
            attach_live_domain(race, documents)
        predictions[str(race_no)] = deepcopy(prediction)
        updated.append(race_no)

    for race in races:
        race_no = integer(race.get("race"))
        if str(race_no) not in predictions and prediction_complete(race.get("prediction")):
            predictions[str(race_no)] = deepcopy(race["prediction"])
    if phase == "preliminary" and sorted(map(int, predictions)) != list(range(1, 13)):
        raise RuntimeError("fukuoka_preliminary_predictions_must_be_12")

    payload["engine"] = ENGINE_ID
    payload["engineVersion"] = ENGINE_VERSION
    payload["preds"] = predictions
    if sorted(map(int, predictions)) == list(range(1, 13)):
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        payload["predictionReason"] = None
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "oddsUsedForPrediction": False,
        "resultUsedForPrediction": False,
        "preliminaryRaceCount": sum(
            prediction.get("phase") == "preliminary"
            for prediction in predictions.values()
            if isinstance(prediction, dict)
        ),
        "finalRaceCount": sum(
            prediction.get("phase") == "final"
            for prediction in predictions.values()
            if isinstance(prediction, dict)
        ),
        "updatedRaces": updated,
        "skippedRaces": skipped,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--stage", choices=("preliminary", "final"), required=True)
    parser.add_argument("--race", type=int)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--live-root", type=Path)
    args = parser.parse_args()
    if args.race is not None and args.race not in range(1, 13):
        raise ValueError("race_must_be_1_to_12")

    data_root = args.data_root if args.data_root.is_absolute() else REPO_ROOT / args.data_root
    dated_path = data_root / "venues" / "fukuoka" / f"{args.date.replace('-', '')}.json"
    latest_path = data_root / "venues" / "fukuoka" / "latest.json"
    if not dated_path.is_file():
        print(json.dumps({"date": args.date, "venue": "fukuoka", "status": "not_running"}))
        return 0
    live_base = args.live_root or data_root / "live" / args.date / "fukuoka"
    if args.live_root is not None and args.race is not None:
        live_base = args.live_root.parent

    payload = apply_predictions(
        load_json(dated_path),
        args.date,
        args.stage,
        live_base,
        args.race,
    )
    atomic_write_json(dated_path, payload)
    latest = load_json(latest_path) if latest_path.is_file() else None
    if latest is None or latest.get("date") == args.date:
        atomic_write_json(latest_path, payload)
    report = {
        "date": args.date,
        "venue": "fukuoka",
        "engine": payload["engine"],
        "engineVersion": payload["engineVersion"],
        "stage": args.stage,
        "predictionStatus": payload.get("predictionStatus"),
        "updatedRaces": payload["predictionEngine"]["updatedRaces"],
        "skippedRaces": payload["predictionEngine"]["skippedRaces"],
        "preliminaryRaceCount": payload["predictionEngine"]["preliminaryRaceCount"],
        "finalRaceCount": payload["predictionEngine"]["finalRaceCount"],
        "datedPath": str(dated_path),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
