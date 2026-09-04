from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPO_ROOT / "engines" / "omura" / "omura_engine_v1_2"
ENGINE_SRC = ENGINE_ROOT / "src"
MASTER_DIR = ENGINE_ROOT / "master_db"
CONFIG_PATH = ENGINE_ROOT / "config" / "engine_config.json"

ENGINE_ID = "omura_engine_v1_9_production"
ENGINE_VERSION = "1.9"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-", "－"):
        return default
    try:
        number = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def ratio(value: Any) -> float:
    number = safe_float(value, 0.0)
    return number / 100.0 if number > 1.0 else max(0.0, number)


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\u3000・･]+", "", text)


def load_complete_live_document(
    path: Path,
    target_date: str,
    race_no: int,
) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(document, dict)
        or document.get("complete") is not True
        or document.get("status") != "complete"
        or document.get("date") != target_date
        or document.get("venue") != "omura"
        or safe_int(document.get("race_no")) != race_no
        or not isinstance(document.get("data"), dict)
    ):
        return None
    return document


def load_live_documents(
    race_dir: Path,
    target_date: str,
    race_no: int,
) -> dict[str, dict[str, Any]] | None:
    documents = {
        name: load_complete_live_document(
            race_dir / f"{name}.json",
            target_date,
            race_no,
        )
        for name in ("direct", "exhibition", "original_exhibition")
    }
    if any(document is None for document in documents.values()):
        return None
    return {
        name: document
        for name, document in documents.items()
        if document is not None
    }


def indexed_live_entries(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        safe_int(entry.get("lane")): entry
        for entry in (document.get("data") or {}).get("entries") or []
        if isinstance(entry, dict) and safe_int(entry.get("lane")) in range(1, 7)
    }


def rank_entries(
    entries: dict[int, dict[str, Any]],
    key: str,
) -> dict[int, int]:
    if set(entries) != set(range(1, 7)):
        raise RuntimeError(f"live_{key}_entries_must_be_6")
    ordered = sorted(
        entries,
        key=lambda lane: (safe_float(entries[lane].get(key), math.inf), lane),
    )
    if any(not math.isfinite(safe_float(entries[lane].get(key), math.inf)) for lane in ordered):
        raise RuntimeError(f"live_{key}_missing")
    return {lane: rank for rank, lane in enumerate(ordered, 1)}


def validate_payload(payload: dict[str, Any], target_date: str) -> None:
    if payload.get("venueId") != "omura" or payload.get("venue") != "大村":
        raise RuntimeError("not_omura_payload")
    if payload.get("date") != target_date:
        raise RuntimeError(
            f"date_mismatch: expected={target_date} actual={payload.get('date')}"
        )

    races = payload.get("races")
    if not isinstance(races, list) or len(races) != 12:
        raise RuntimeError("omura_races_must_be_12")

    race_numbers = sorted(safe_int(race.get("race")) for race in races)
    if race_numbers != list(range(1, 13)):
        raise RuntimeError(f"invalid_race_numbers: {race_numbers}")

    for race in races:
        racers = race.get("racers") or race.get("entries")
        if not isinstance(racers, list) or len(racers) != 6:
            raise RuntimeError(f"race_{race.get('race')}_racers_must_be_6")


def load_player_index() -> dict[str, str]:
    import pandas as pd

    path = MASTER_DIR / "omura_player_course_db_v6_1.csv"
    if not path.exists():
        raise FileNotFoundError(f"player master missing: {path}")

    frame = pd.read_csv(path, encoding="utf-8-sig", dtype={"player_id": str})
    required = {"player_id", "player_name"}
    if not required.issubset(frame.columns):
        raise RuntimeError(f"player master columns missing: {required - set(frame.columns)}")

    index: dict[str, str] = {}
    duplicates: set[str] = set()

    for row in frame[["player_id", "player_name"]].dropna().drop_duplicates().itertuples():
        key = normalize_name(row.player_name)
        player_id = str(row.player_id).replace(".0", "").zfill(4)
        if key in index and index[key] != player_id:
            duplicates.add(key)
        else:
            index[key] = player_id

    for key in duplicates:
        index.pop(key, None)
    return index


def resolve_player_id(racer: dict[str, Any], player_index: dict[str, str]) -> str:
    for key in ("player_id", "playerId", "registration_no", "registrationNo", "reg_no"):
        value = racer.get(key)
        if value not in (None, "", "-"):
            return str(value).replace(".0", "").zfill(4)

    name_key = normalize_name(racer.get("name"))
    player_id = player_index.get(name_key)
    if player_id:
        return player_id

    # A newly registered or previously unseen racer may not yet exist in the
    # player-course master. Use a non-numeric deterministic identifier so all
    # player-specific master lookups safely miss, while the race can still be
    # predicted from class, national/local rates, motor and water conditions.
    return f"UNRESOLVED:{name_key or 'UNKNOWN'}"


def series_day(payload: dict[str, Any], race: dict[str, Any]) -> int:
    for value in (
        race.get("eventDay"),
        (race.get("race_meta") or {}).get("day_no"),
        payload.get("eventDay"),
        payload.get("day_no"),
    ):
        day = safe_int(value)
        if day >= 1:
            return day

    label = str(race.get("eventDayLabel") or payload.get("seriesDay") or "")
    match = re.search(r"(\d+)", label)
    if match:
        return max(1, int(match.group(1)))
    if "初日" in label:
        return 1
    if "最終" in label:
        return 6
    return 1


def meeting_form_score(racer: dict[str, Any]) -> float:
    runs = racer.get("season_runs") or []
    if not isinstance(runs, list) or not runs:
        return 0.0

    scores: list[float] = []
    for run in runs[-6:]:
        if isinstance(run, dict):
            finish = safe_int(
                run.get("finish")
                or run.get("rank")
                or run.get("arrival")
                or run.get("着")
            )
        else:
            finish = safe_int(run)
        if finish == 1:
            scores.append(0.55)
        elif finish == 2:
            scores.append(0.32)
        elif finish == 3:
            scores.append(0.16)
        elif finish in (4, 5):
            scores.append(-0.08)
        elif finish >= 6:
            scores.append(-0.22)

    if not scores:
        return 0.0
    return round(max(-0.6, min(0.6, sum(scores) / len(scores))), 3)


def motor_proxy(racer: dict[str, Any]) -> dict[str, Any]:
    motor_2 = safe_float(racer.get("motor_2"), 0.0)
    motor_3 = safe_float(racer.get("motor_3"), 0.0)

    # Morning data has motor 2-ren/3-ren rates, not the official recent-10 detail.
    # Keep this bounded and explicitly mark it as a proxy.
    form = ((motor_2 - 30.0) / 25.0) * 0.65 + ((motor_3 - 45.0) / 35.0) * 0.35
    form = max(-0.65, min(0.65, form))

    return {
        "form_score": round(form, 3),
        "straight_score": 0.0,
        "turn_score": round(form * 0.45, 3),
        "source": "morning_motor_rate_proxy",
    }


def kimarite_for(racer: dict[str, Any], lane: int) -> dict[str, float]:
    values: dict[str, float] = {}

    if lane == 1:
        values["nige_rate"] = ratio(
            racer.get("boaters_escape_rate")
            or racer.get("boaters_nige_rate")
        )
        values["escaped_against_rate"] = ratio(
            racer.get("boaters_sashare_rate")
            or racer.get("boaters_sasare_rate")
        )
        values["attacked_against_rate"] = ratio(
            safe_float(racer.get("boaters_makurare_rate"))
            + safe_float(racer.get("boaters_makurare_zashi_rate"))
        )
    else:
        values["sashi_rate"] = ratio(racer.get("boaters_sashi_rate"))
        values["makuri_rate"] = ratio(racer.get("boaters_makuri_rate"))
        values["makurizashi_rate"] = ratio(
            racer.get("boaters_makuri_sashi_rate")
            or racer.get("boaters_makurizashi_rate")
        )

    return {key: value for key, value in values.items() if value > 0.0}


def tide_direction(value: Any) -> str:
    text = str(value or "").lower()
    if any(token in text for token in ("上げ", "rising", "rise", "up")):
        return "rising"
    if any(token in text for token in ("下げ", "falling", "fall", "down")):
        return "falling"
    if any(token in text for token in ("止", "停", "slack", "stop", "極")):
        return "stop"
    return "unknown"


def water_input(payload: dict[str, Any], race: dict[str, Any]) -> dict[str, Any]:
    live = race.get("live") or {}
    weather = race.get("weather") or live.get("weather") or {}
    tide = race.get("tide") or payload.get("tide") or {}

    raw_height = (
        race.get("normalized_tide_height")
        or tide.get("normalized_tide_height")
        or tide.get("normalizedHeight")
    )
    normalized_height = safe_float(raw_height, 0.5)
    if normalized_height > 1.0:
        normalized_height /= 100.0
    normalized_height = max(0.0, min(1.0, normalized_height))

    minutes = safe_int(
        race.get("minutes_from_nearest_extreme")
        or tide.get("minutes_from_nearest_extreme")
        or tide.get("minutesFromNearestExtreme"),
        999,
    )

    direction_source = (
        race.get("tide_phase")
        or tide.get("phase")
        or tide.get("direction")
        or tide.get("label")
    )

    return {
        "tide_type": (
            tide.get("tideType")
            or tide.get("tide_type")
            or tide.get("type")
            or ""
        ),
        "tide_direction": tide_direction(direction_source),
        "normalized_tide_height": normalized_height,
        "minutes_from_nearest_extreme": minutes,
        "wind_direction": (
            weather.get("wind_direction")
            or weather.get("windDirection")
            or weather.get("wind_dir")
            or ""
        ),
        "wind_speed_mps": safe_float(
            weather.get("wind_speed")
            or weather.get("windSpeed")
            or weather.get("wind"),
            0.0,
        ),
        "wave_height_cm": safe_float(
            weather.get("wave_height")
            or weather.get("waveHeight")
            or weather.get("wave"),
            0.0,
        ),
        "stabilizer": bool(
            live.get("stabilizer")
            or race.get("stabilizer")
            or weather.get("stabilizer")
        ),
        "shortened_laps": bool(
            live.get("shortened_laps")
            or race.get("shortened_laps")
            or weather.get("shortened_laps")
        ),
    }


def boat_input(
    racer: dict[str, Any],
    player_index: dict[str, str],
) -> dict[str, Any]:
    lane = safe_int(racer.get("lane"))
    entry_course = safe_int(
        racer.get("actual_course") or racer.get("entry_course") or lane,
        lane,
    )

    boat = {
        "lane": lane,
        "entry_course": entry_course,
        "player_id": resolve_player_id(racer, player_index),
        "player_name": racer.get("name") or "",
        "grade": racer.get("class") or racer.get("grade") or "B1",
        "national_win_rate": safe_float(racer.get("nat_win"), 0.0),
        "local_win_rate": safe_float(racer.get("local_win"), 0.0),
        "motor_no": safe_int(racer.get("motor_no")),
        "boat_no": safe_int(racer.get("boat_no")),
        "motor_recent10": motor_proxy(racer),
        "kimarite": kimarite_for(racer, lane),
        "meeting": {"form_score": meeting_form_score(racer)},
    }

    # Exhibition is intentionally not fabricated during the morning phase.
    # The engine handles missing exhibition fields as neutral.
    if isinstance(racer.get("exhibition"), dict):
        boat["exhibition"] = racer["exhibition"]
    if racer.get("tilt") not in (None, "", "-"):
        boat["tilt"] = safe_float(racer.get("tilt"))

    return boat


def build_engine_input(
    payload: dict[str, Any],
    race: dict[str, Any],
    player_index: dict[str, str],
) -> dict[str, Any]:
    racers = race.get("racers") or race.get("entries") or []
    return {
        "race": {
            "venue": "omura",
            "race_date": payload["date"],
            "race_no": safe_int(race.get("race")),
            "day_no": series_day(payload, race),
            "deadline_time_jst": race.get("deadline") or "",
            "is_fixed_entry": bool(race.get("is_fixed_entry", False)),
            "actual_entry_confirmed": not bool(race.get("entry_changes")),
        },
        "water": water_input(payload, race),
        "boats": [boat_input(racer, player_index) for racer in racers],
    }


def apply_live_documents_to_race(
    race: dict[str, Any],
    documents: dict[str, dict[str, Any]],
) -> None:
    direct = documents["direct"]["data"]
    exhibition = indexed_live_entries(documents["exhibition"])
    original = indexed_live_entries(documents["original_exhibition"])
    if set(exhibition) != set(range(1, 7)):
        raise RuntimeError("live_exhibition_entries_must_be_6")

    original_ranks = rank_entries(original, "sum")
    actual_entry = [safe_int(lane) for lane in direct.get("actual_entry") or []]
    if sorted(actual_entry) != list(range(1, 7)):
        raise RuntimeError("live_actual_entry_must_be_permutation_1_to_6")
    course_by_lane = {
        lane: course for course, lane in enumerate(actual_entry, 1)
    }
    direct_racers = {
        safe_int(entry.get("lane")): entry
        for entry in direct.get("racers") or []
        if isinstance(entry, dict) and safe_int(entry.get("lane")) in range(1, 7)
    }

    racers = race.get("racers") or race.get("entries") or []
    if len(racers) != 6:
        raise RuntimeError("omura_live_racers_must_be_6")
    for racer in racers:
        lane = safe_int(racer.get("lane"))
        ex = exhibition.get(lane) or {}
        direct_racer = direct_racers.get(lane) or {}
        time_rank = safe_int(ex.get("exhibition_rank"))
        if time_rank not in range(1, 7):
            raise RuntimeError(f"live_exhibition_rank_invalid_lane_{lane}")
        racer["actual_course"] = course_by_lane[lane]
        racer["entry_course"] = course_by_lane[lane]
        racer["tilt"] = safe_float(
            direct_racer.get("tilt", ex.get("tilt")),
            safe_float(racer.get("tilt")),
        )
        racer["exhibition"] = {
            "time_rank": time_rank,
            "st": safe_float(ex.get("start_time", ex.get("start_raw")), 9.0),
            "slit_type": "横一線",
            "original_sum_rank": original_ranks[lane],
        }

    race["entry_changes"] = []
    race["weather"] = deepcopy(direct)
    race["live"] = {
        "direct": deepcopy(direct),
        "weather": deepcopy(direct),
        "exhibition": deepcopy(documents["exhibition"]["data"]),
        "original": deepcopy(documents["original_exhibition"]["data"]),
        "original_exhibition": deepcopy(
            documents["original_exhibition"]["data"]
        ),
    }


def ticket_row(item: dict[str, Any], role: str) -> dict[str, Any]:
    combo = str(item.get("ticket") or item.get("combo") or "")
    probability = safe_float(item.get("probability"), 0.0)
    return {
        "combo": combo,
        "role": role,
        "probability": round(probability * 100.0, 3),
        "odds": "-",
    }


def site_prediction(result: dict[str, Any]) -> dict[str, Any]:
    rows = result["probabilities"]["boats"]
    win = {str(row["lane"]): round(float(row["win_percent"]), 2) for row in rows}
    second = {
        str(row["lane"]): round(float(row["second_percent"]), 2)
        for row in rows
    }
    third = {
        str(row["lane"]): round(float(row["third_percent"]), 2)
        for row in rows
    }
    top3 = {
        str(row["lane"]): round(float(row["top3_percent"]), 2)
        for row in rows
    }

    ticket_groups = result.get("tickets") or {}
    main = [ticket_row(item, "本線") for item in ticket_groups.get("main") or []]
    deviation = [
        ticket_row(item, "ズレ対応")
        for item in ticket_groups.get("deviation") or []
    ]
    upset = [
        ticket_row(item, "荒れ対応")
        for item in ticket_groups.get("upset") or []
    ]
    all_tickets = main + deviation + upset

    sab_detail = result.get("sab") or {}
    sab_grade = str(sab_detail.get("rank") or sab_detail.get("grade") or "B")
    confidence = round(safe_float(sab_detail.get("score"), 0.0), 1)

    scenario = result.get("scenario") or {}
    primary = scenario.get("primary") or ""
    scenario_probabilities = scenario.get("probabilities") or {}

    return {
        "status": "complete",
        "engine": ENGINE_ID,
        "engineVersion": ENGINE_VERSION,
        "win": win,
        "second": second,
        "third": third,
        "top3": top3,
        "sab": sab_grade,
        "sabDetail": sab_detail,
        "confidence": confidence,
        "ai": main + deviation,
        "aiUpset": upset,
        "tickets": all_tickets,
        "scenarios": scenario,
        "sourceSummary": {
            "stage": "morning",
            "primaryScenario": primary,
            "primaryScenarioProbability": round(
                safe_float(scenario_probabilities.get(primary), 0.0) * 100.0,
                2,
            ),
            "dataCompleteness": safe_float(
                (result.get("data_quality") or {}).get("completeness"),
                0.0,
            ),
            "missingFields": (
                result.get("data_quality") or {}
            ).get("missing_fields", []),
            "oddsUsedForProbability": False,
            "exhibitionStartUsedAlone": False,
            "motorRecent10Source": "morning_motor_rate_proxy",
        },
        "probabilityFlow": {
            "baseApplied": True,
            "masterApplied": True,
            "tideWindWaveApplied": True,
            "realtimeApplied": False,
            "normalized": True,
        },
        "predictionStage": {
            "label": "事前予想",
            "statusText": "大村v1.9：事前データ・マスターDB・潮風波・展開連動反映",
            "badge": "事前予想",
            "color": "blue",
        },
        "odds": {},
        "oddsUsedForPrediction": False,
    }


def prediction_complete(prediction: dict[str, Any]) -> bool:
    required = (
        "win",
        "second",
        "third",
        "sab",
        "ai",
        "aiUpset",
        "sourceSummary",
    )
    if not all(key in prediction for key in required):
        return False

    for key in ("win", "second", "third"):
        values = prediction.get(key)
        if not isinstance(values, dict):
            return False
        if sorted(safe_int(lane) for lane in values) != list(range(1, 7)):
            return False
        if abs(sum(safe_float(value) for value in values.values()) - 100.0) > 0.25:
            return False

    tickets = list(prediction.get("ai") or []) + list(
        prediction.get("aiUpset") or []
    )
    if len(tickets) != 10:
        return False
    combos = [ticket.get("combo") for ticket in tickets]
    if any(not combo for combo in combos) or len(set(combos)) != len(combos):
        return False
    return True


def mark_final_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    prediction["phase"] = "final"
    prediction["finalPredictionStatus"] = "complete"
    prediction["sourceSummary"].update(
        {
            "stage": "live",
            "liveInputsComplete": True,
            "liveSources": [
                "direct",
                "exhibition",
                "original_exhibition",
            ],
            "oddsUsedForProbability": False,
        }
    )
    prediction["probabilityFlow"]["realtimeApplied"] = True
    prediction["predictionStage"] = {
        "label": "本予想",
        "statusText": "大村v1.9：直前情報3点を反映して再計算済み",
        "badge": "本予想",
        "color": "green",
    }
    return prediction


def apply_omura_live_race(
    payload: dict[str, Any],
    target_date: str,
    race_no: int,
    race_dir: Path,
) -> tuple[dict[str, Any], bool]:
    validate_payload(payload, target_date)
    if race_no not in range(1, 13):
        raise ValueError("race_must_be_1_to_12")

    documents = load_live_documents(race_dir, target_date, race_no)
    if documents is None:
        return payload, False

    candidate = deepcopy(payload)
    race = next(
        item for item in candidate["races"] if safe_int(item.get("race")) == race_no
    )
    apply_live_documents_to_race(race, documents)

    if str(ENGINE_SRC) not in sys.path:
        sys.path.insert(0, str(ENGINE_SRC))
    from omura_engine import OmuraPredictionEngine

    engine = OmuraPredictionEngine(MASTER_DIR, CONFIG_PATH)
    player_index = load_player_index()
    result = engine.predict(build_engine_input(candidate, race, player_index))
    prediction = mark_final_prediction(site_prediction(result))
    if not prediction_complete(prediction):
        raise RuntimeError("prediction_output_incomplete")

    previous = deepcopy(race.get("prediction"))
    if isinstance(previous, dict) and "predictionPre" not in race:
        race["predictionPre"] = previous
    race["predictionFinal"] = deepcopy(prediction)
    race["prediction"] = deepcopy(prediction)

    predictions = deepcopy(candidate.get("preds") or {})
    predictions[str(race_no)] = deepcopy(prediction)
    candidate["preds"] = predictions
    candidate["engine"] = ENGINE_ID
    candidate["engineVersion"] = ENGINE_VERSION
    candidate["predictionStatus"] = "ready"
    candidate["predictionReason"] = None
    metadata = deepcopy(candidate.get("predictionEngine") or {})
    metadata.update(
        {
            "id": ENGINE_ID,
            "version": ENGINE_VERSION,
            "master": "omura_master_db_v6_1",
            "generatedBy": "automation/apply_omura_v1.py",
            "oddsUsedForProbability": False,
            "exhibitionStartUsedAlone": False,
            "updatedRaces": [race_no],
            "finalRaceCount": sum(
                isinstance(value, dict) and value.get("phase") == "final"
                for value in predictions.values()
            ),
        }
    )
    candidate["predictionEngine"] = metadata
    return candidate, True


def apply_omura_v1(
    payload: dict[str, Any],
    target_date: str,
) -> dict[str, Any]:
    validate_payload(payload, target_date)

    if str(ENGINE_SRC) not in sys.path:
        sys.path.insert(0, str(ENGINE_SRC))

    from omura_engine import OmuraPredictionEngine

    engine = OmuraPredictionEngine(MASTER_DIR, CONFIG_PATH)
    player_index = load_player_index()
    predictions: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    for race in payload["races"]:
        race_no = safe_int(race.get("race"))
        try:
            engine_input = build_engine_input(payload, race, player_index)
            result = engine.predict(engine_input)
            prediction = site_prediction(result)
            if not prediction_complete(prediction):
                raise RuntimeError("prediction_output_incomplete")
            predictions[str(race_no)] = prediction
            race["prediction"] = prediction
        except Exception as exc:
            failures.append(
                {
                    "race": race_no,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    if failures:
        raise RuntimeError(
            "omura_v1_generation_failed: "
            + json.dumps(failures, ensure_ascii=False)
        )
    if sorted(safe_int(key) for key in predictions) != list(range(1, 13)):
        raise RuntimeError("omura_v1_predictions_must_be_12")

    payload["engine"] = ENGINE_ID
    payload["engineVersion"] = ENGINE_VERSION
    payload["preds"] = predictions
    payload["predictionStatus"] = "ready"
    payload["predictionReason"] = None
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "master": "omura_master_db_v6_1",
        "generatedBy": "automation/apply_omura_v1.py",
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
        "raceCount": 12,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--stage",
        choices=("morning", "final"),
        default="morning",
    )
    parser.add_argument("--race", type=int)
    parser.add_argument("--live-root", type=Path)
    parser.add_argument(
        "--require-open",
        action="store_true",
        help="Fail when Omura JSON does not exist instead of skipping.",
    )
    args = parser.parse_args()

    date_dir = args.date.replace("-", "")
    data_root = Path(args.data_root)
    dated_path = data_root / "venues" / "omura" / f"{date_dir}.json"
    latest_path = data_root / "venues" / "omura" / "latest.json"

    if not dated_path.exists():
        message = f"Omura data is not open: {dated_path}"
        if args.require_open:
            raise FileNotFoundError(message)
        print(message)
        return 0

    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    if args.stage == "final":
        if args.race not in range(1, 13):
            raise ValueError("final_stage_requires_race_1_to_12")
        race_dir = args.live_root or (
            data_root
            / "live"
            / args.date
            / "omura"
            / f"{args.race:02d}"
        )
        payload, updated = apply_omura_live_race(
            payload,
            args.date,
            args.race,
            race_dir,
        )
        if not updated:
            print(
                json.dumps(
                    {
                        "date": args.date,
                        "venue": "omura",
                        "stage": "final",
                        "race": args.race,
                        "status": "waiting_live_data",
                        "updated": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
    else:
        payload = apply_omura_v1(payload, args.date)

    atomic_write_json(dated_path, payload)
    latest = (
        json.loads(latest_path.read_text(encoding="utf-8"))
        if latest_path.is_file()
        else None
    )
    if latest is None or latest.get("date") == args.date:
        atomic_write_json(latest_path, payload)

    summary = {
        "date": args.date,
        "engine": payload["engine"],
        "engineVersion": payload["engineVersion"],
        "stage": args.stage,
        "race": args.race,
        "raceCount": len(payload["preds"]),
        "datedPath": str(dated_path),
        "latestPath": str(latest_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
