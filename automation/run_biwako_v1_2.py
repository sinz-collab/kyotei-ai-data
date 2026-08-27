from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "biwako_v1_2"
CONFIG_PATH = REPO_ROOT / "engines" / "biwako_v1_1" / "biwako_correction_v1_1.json"
DEFAULT_DB_PATH = REPO_ROOT / "data" / "venues" / "biwako" / "db" / "biwako_ai_master.sqlite"
ENGINE_ID = "biwako_engine_v1.2"
ENGINE_VERSION = "1.2"

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from biwako_prediction_engine_v1_2 import BiwakoPredictionEngineV12  # noqa: E402


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


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\u3000・･]+", "", text)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required: {path}")
    return value


def complete_document(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        document = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if document.get("complete") is not True or document.get("status") != "complete":
        return None
    return document if isinstance(document.get("data"), dict) else None


class PlayerIdResolver:
    def __init__(self, db_path: Path):
        self.connection = sqlite3.connect(str(db_path))
        self.by_name: dict[str, str] = {}
        columns = {
            row[1]
            for row in self.connection.execute("PRAGMA table_info(race_history)")
        }
        if {"reg_no", "racer_name"}.issubset(columns):
            rows = self.connection.execute(
                "SELECT racer_name, reg_no, MAX(date) "
                "FROM race_history WHERE racer_name IS NOT NULL "
                "GROUP BY racer_name, reg_no"
            )
            for name, reg_no, _ in rows:
                key = normalize_name(name)
                if key and reg_no not in (None, ""):
                    self.by_name[key] = str(reg_no).strip().split(".")[0].zfill(4)

    def resolve(self, racer: dict, lane: int) -> tuple[str, bool]:
        for key in ("reg_no", "player_id", "registration_no", "racer_id"):
            value = racer.get(key)
            if value not in (None, ""):
                return str(value).strip().split(".")[0].zfill(4), True
        resolved = self.by_name.get(normalize_name(racer.get("name")))
        if resolved:
            return resolved, True
        return f"unresolved-{lane}-{normalize_name(racer.get('name'))}", False

    def close(self) -> None:
        self.connection.close()


def live_documents(race_dir: Path) -> dict[str, dict] | None:
    documents = {
        name: complete_document(race_dir / f"{name}.json")
        for name in ("direct", "exhibition", "original_exhibition")
    }
    if any(document is None for document in documents.values()):
        return None
    return {name: document for name, document in documents.items() if document is not None}


def indexed_entries(document: dict, key: str = "entries") -> dict[int, dict]:
    data = document.get("data") or {}
    return {
        integer(entry.get("lane")): entry
        for entry in data.get(key) or []
        if isinstance(entry, dict) and integer(entry.get("lane")) in range(1, 7)
    }


def apply_live_input(race_input: dict, documents: dict[str, dict]) -> None:
    direct = documents["direct"]["data"]
    exhibition = indexed_entries(documents["exhibition"])
    original = indexed_entries(documents["original_exhibition"])
    direct_racers = {
        integer(entry.get("lane")): entry
        for entry in direct.get("racers") or []
        if isinstance(entry, dict)
    }
    actual_entry = direct.get("actual_entry") or list(range(1, 7))
    course_by_lane = {
        integer(lane): course
        for course, lane in enumerate(actual_entry, 1)
        if integer(lane) in range(1, 7)
    }

    race_input["wind_direction"] = direct.get("wind_direction") or "unknown"
    race_input["wind_speed"] = number(direct.get("wind_speed"), 0.0)
    race_input["wave_height"] = number(direct.get("wave_height"), 0.0)

    for boat in race_input["boats"]:
        lane = boat["lane"]
        ex = exhibition[lane]
        oe = original[lane]
        direct_racer = direct_racers.get(lane) or {}
        boat["actual_course"] = integer(
            ex.get("exhibition_course"),
            course_by_lane.get(lane, lane),
        )
        if direct_racer.get("player_id") not in (None, ""):
            boat["reg_no"] = str(direct_racer["player_id"])
        boat["exhibition_time"] = number(ex.get("exhibition_time"))
        boat["exhibition_st"] = number(
            ex.get("start_time", ex.get("start_raw"))
        )
        boat["original_lap"] = number(oe.get("lap_time"))
        boat["original_turn"] = number(oe.get("turn_time"))
        boat["original_straight"] = number(oe.get("straight_time"))
        lap = boat["original_lap"]
        exhibition_time = boat["exhibition_time"]
        boat["original_sum"] = (
            round(lap + exhibition_time, 4)
            if lap is not None and exhibition_time is not None
            else None
        )


def build_engine_input(
    payload: dict,
    race: dict,
    resolver: PlayerIdResolver,
    documents: dict[str, dict] | None = None,
) -> tuple[dict, list[dict]]:
    unresolved = []
    boats = []
    for racer in sorted(race.get("racers") or [], key=lambda item: integer(item.get("lane"))):
        lane = integer(racer.get("lane"))
        reg_no, resolved = resolver.resolve(racer, lane)
        if not resolved:
            unresolved.append({"race": integer(race.get("race")), "lane": lane, "name": racer.get("name")})
        boats.append(
            {
                "lane": lane,
                "actual_course": integer(
                    racer.get("actual_course") or racer.get("entry_course"),
                    lane,
                ),
                "reg_no": reg_no,
                "name": racer.get("name") or "",
                "national_win_rate": number(racer.get("nat_win"), 0.0),
                "local_win_rate": number(racer.get("local_win"), 0.0),
                "motor_no": integer(racer.get("motor_no")),
                "motor_top2_rate": number(racer.get("motor_2"), 0.0),
                "motor_top3_rate": number(racer.get("motor_3"), 0.0),
                "setsukan_runs": deepcopy(racer.get("season_runs") or []),
                "boaters_escape_rate": racer.get("boaters_escape_rate"),
                "boaters_sashare_rate": racer.get("boaters_sashare_rate"),
                "boaters_makurare_rate": racer.get("boaters_makurare_rate"),
                "boaters_makurare_zashi_rate": racer.get("boaters_makurare_zashi_rate"),
                "boaters_sashi_rate": racer.get("boaters_sashi_rate"),
                "boaters_makuri_rate": racer.get("boaters_makuri_rate"),
                "boaters_makurizashi_rate": racer.get("boaters_makurizashi_rate"),
            }
        )
    value = {
        "date": payload["date"],
        "race_no": integer(race.get("race")),
        "event_id": f"biwako_{payload['date'].replace('-', '')}",
        "event_day": integer(race.get("eventDay") or payload.get("eventDay"), 1),
        "final_day_flag": "最終日" in str(race.get("eventDayLabel") or payload.get("eventDayLabel") or ""),
        "wind_direction": "unknown",
        "wind_speed": None,
        "wave_height": None,
        "boats": boats,
    }
    if documents is not None:
        apply_live_input(value, documents)
    return value, unresolved


def pct_map(result: dict, key: str) -> dict[str, float]:
    return {
        str(item["lane"]): round(float(item[key]) * 100.0, 2)
        for item in result["boats"]
    }


def ticket_rows(result: dict) -> tuple[list[dict], list[dict], list[dict]]:
    score_by_ticket = {
        row["ticket"]: round(float(row.get("score") or 0.0) * 100.0, 2)
        for row in result["tickets"].get("ranked_top20") or []
    }

    def rows(key: str, role: str) -> list[dict]:
        return [
            {"combo": combo, "role": role, "prob": score_by_ticket.get(combo, 0.0)}
            for combo in result["tickets"][key]
        ]

    return rows("main", "本線"), rows("deviation", "ずらし"), rows("upset", "荒れ")


def format_prediction(result: dict, phase: str) -> dict:
    ai, balance, upset = ticket_rows(result)
    is_final = phase == "final"
    stage = {
        "label": "本予想" if is_final else "仮予想",
        "badge": "本予想" if is_final else "仮予想",
        "statusText": (
            "直前・展示・オリジナル展示を反映してびわこv1.2で再予想済み"
            if is_final
            else "前データをびわこv1.2へ反映。直前データ取得後に本予想へ更新"
        ),
        "color": "green" if is_final else "yellow",
    }
    return {
        "status": "complete",
        "engine": ENGINE_ID,
        "engineVersion": ENGINE_VERSION,
        "engine_version": result.get("engine_version", ENGINE_ID),
        "parameter_version": result.get("parameter_version"),
        "phase": phase,
        "finalPredictionStatus": "complete" if is_final else "waiting_live_data",
        "predictionStage": stage,
        "win": pct_map(result, "win_prob"),
        "second": pct_map(result, "second_prob"),
        "third": pct_map(result, "third_prob"),
        "top3": pct_map(result, "top3_prob"),
        "sab": result["sab"]["grade"],
        "sabDetail": deepcopy(result["sab"]),
        "ai": ai,
        "balance": balance,
        "aiUpset": upset,
        "tickets": ai + balance + upset,
        "scenario": deepcopy(result.get("scenario") or {}),
        "attackDefense": deepcopy(result.get("attack_defense") or {}),
        "liveAdjustment": deepcopy(result.get("live_adjustment")),
        "rules": deepcopy(result.get("rules") or {}),
        "probabilityFlow": {
            "required": True,
            "baseApplied": True,
            "baseLabel": "びわこv1.2仮予想",
            "realtimeApplied": is_final,
            "realtimeLabel": "進入・展示・スリット・オリジナル展示反映",
            "reviewed": is_final,
            "reviewLabel": "びわこv1.2本予想",
        },
        "diagnostics": {
            "oddsUsedForPrediction": False,
            "resultUsedForPrediction": False,
            "engineStage": result.get("stage"),
        },
    }


def prediction_complete(prediction: Any, phase: str | None = None) -> bool:
    if not isinstance(prediction, dict) or prediction.get("engine") != ENGINE_ID:
        return False
    if phase is not None and prediction.get("phase") != phase:
        return False
    return all(isinstance(prediction.get(key), dict) and len(prediction[key]) == 6 for key in ("win", "second", "third"))


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


def sync_race_prediction(race: dict, prediction: dict, phase: str) -> None:
    if phase == "preliminary":
        race["predictionPre"] = deepcopy(prediction)
        race["predictionFinal"] = None
    else:
        race["predictionFinal"] = deepcopy(prediction)
    race["prediction"] = deepcopy(prediction)


def validate_payload(payload: dict, target_date: str) -> None:
    if payload.get("date") != target_date or payload.get("venueId") != "biwako":
        raise RuntimeError("biwako_payload_identity_invalid")
    races = payload.get("races") or []
    if len(races) != 12 or sorted(integer(race.get("race")) for race in races) != list(range(1, 13)):
        raise RuntimeError("biwako_races_must_be_12")


def apply_predictions(
    payload: dict,
    target_date: str,
    engine: BiwakoPredictionEngineV12,
    resolver: PlayerIdResolver,
    stage: str,
    live_base: Path,
    race_filter: int | None = None,
) -> dict:
    validate_payload(payload, target_date)
    predictions = deepcopy(payload.get("preds") or {})
    unresolved_all = []
    updated = []
    skipped = []

    for race in payload["races"]:
        race_no = integer(race.get("race"))
        if race_filter is not None and race_no != race_filter:
            continue
        existing_active = race.get("prediction")

        if stage == "preliminary" and prediction_complete(existing_active, "final"):
            predictions[str(race_no)] = deepcopy(existing_active)
            skipped.append(race_no)
            continue

        documents = None
        if stage == "final":
            documents = live_documents(live_base / f"{race_no:02d}")
            if documents is None:
                if prediction_complete(existing_active):
                    predictions[str(race_no)] = deepcopy(existing_active)
                skipped.append(race_no)
                continue

        race_input, unresolved = build_engine_input(payload, race, resolver, documents)
        unresolved_all.extend(unresolved)
        result = engine.predict(race_input, stage)
        prediction = format_prediction(result, stage)
        if stage == "final" and not prediction_complete(race.get("predictionPre"), "preliminary"):
            pre_input, pre_unresolved = build_engine_input(payload, race, resolver)
            unresolved_all.extend(pre_unresolved)
            race["predictionPre"] = format_prediction(
                engine.predict(pre_input, "preliminary"),
                "preliminary",
            )
        if stage == "final":
            add_probability_review(prediction, race["predictionPre"])
        sync_race_prediction(race, prediction, stage)
        predictions[str(race_no)] = deepcopy(prediction)
        updated.append(race_no)

    for race in payload["races"]:
        race_no = integer(race.get("race"))
        if str(race_no) not in predictions and prediction_complete(race.get("prediction")):
            predictions[str(race_no)] = deepcopy(race["prediction"])

    if stage == "preliminary" and sorted(map(int, predictions)) != list(range(1, 13)):
        raise RuntimeError("biwako_preliminary_predictions_must_be_12")

    payload["engine"] = ENGINE_ID
    payload["engineVersion"] = ENGINE_VERSION
    payload["engine_version"] = ENGINE_ID
    payload["preds"] = predictions
    if sorted(map(int, predictions)) == list(range(1, 13)):
        payload["predictionAvailable"] = True
        payload["predictionStatus"] = "ready"
        payload["predictionReason"] = None
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "engine_version": ENGINE_ID,
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
        "playerIdUnresolved": unresolved_all,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--stage", choices=("preliminary", "final"), required=True)
    parser.add_argument("--race", type=int)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--live-root", type=Path)
    args = parser.parse_args()
    if args.race is not None and args.race not in range(1, 13):
        raise ValueError("race_must_be_1_to_12")

    data_root = args.data_root if args.data_root.is_absolute() else REPO_ROOT / args.data_root
    db_path = args.db_path if args.db_path.is_absolute() else REPO_ROOT / args.db_path
    dated_path = data_root / "venues" / "biwako" / f"{args.date.replace('-', '')}.json"
    latest_path = data_root / "venues" / "biwako" / "latest.json"
    if not dated_path.is_file():
        print(json.dumps({
            "date": args.date,
            "venue": "biwako",
            "stage": args.stage,
            "status": "not_running",
            "updatedRaces": [],
        }, ensure_ascii=False, indent=2))
        return 0
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    live_base = args.live_root or data_root / "live" / args.date / "biwako"
    if args.live_root is not None and args.race is not None:
        live_base = args.live_root.parent

    payload = load_json(dated_path)
    resolver = PlayerIdResolver(db_path)
    engine = BiwakoPredictionEngineV12(db_path, CONFIG_PATH)
    try:
        payload = apply_predictions(
            payload,
            args.date,
            engine,
            resolver,
            args.stage,
            live_base,
            args.race,
        )
    finally:
        engine.close()
        resolver.close()

    atomic_write_json(dated_path, payload)
    latest = load_json(latest_path) if latest_path.is_file() else None
    if latest is None or latest.get("date") == args.date:
        atomic_write_json(latest_path, payload)

    report = {
        "date": args.date,
        "venue": "biwako",
        "engine": payload["engine"],
        "engineVersion": payload["engineVersion"],
        "engine_version": payload["engine_version"],
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
