from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "toda_v5"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_prediction_engine_v5 import ENGINE_ID, TodaPredictionEngineV5


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def validate_payload(payload: dict, target_date: str) -> None:
    if payload.get("venueId") != "toda" or payload.get("venue") != "戸田":
        raise RuntimeError("not_toda_payload")
    if payload.get("date") != target_date:
        raise RuntimeError(f"date_mismatch: expected={target_date} actual={payload.get('date')}")
    races = payload.get("races")
    if not isinstance(races, list) or len(races) != 12:
        raise RuntimeError("toda_races_must_be_12")
    if sorted(int(race.get("race") or 0) for race in races) != list(range(1, 13)):
        raise RuntimeError("invalid_race_numbers")
    for race in races:
        if not isinstance(race.get("racers"), list) or len(race["racers"]) != 6:
            raise RuntimeError(f"race_{race.get('race')}_racers_must_be_6")


def context_for(payload: dict, race: dict) -> dict:
    weather = race.get("weather") or (race.get("live") or {}).get("weather") or {}
    tide = race.get("tide") or payload.get("tide") or {}
    return {
        "wind_speed": weather.get("wind_speed", weather.get("wind")),
        "wind_direction": weather.get("wind_direction", weather.get("windDirection")),
        "wave_height": weather.get("wave_height", weather.get("wave")),
        "tide_phase": race.get("tide_phase") or tide.get("phase") or tide.get("label"),
        "tide_type": tide.get("tideType") or tide.get("tide_type") or tide.get("type"),
        "event_day": race.get("eventDay") or payload.get("eventDay"),
    }


def prediction_complete(prediction: dict) -> bool:
    required = ("win", "second", "third", "sab", "ai", "aiUpset", "sourceSummary")
    if not all(key in prediction for key in required):
        return False
    for key in ("win", "second", "third"):
        values = prediction.get(key)
        if not isinstance(values, dict) or sorted(map(int, values.keys())) != list(range(1, 7)):
            return False
        if abs(sum(float(v) for v in values.values()) - 100.0) > 0.5:
            return False
    return True


def apply_toda_v5(payload: dict, target_date: str) -> dict:
    validate_payload(payload, target_date)
    engine = TodaPredictionEngineV5()
    predictions = {}
    failures = []
    for race in payload["races"]:
        race_no = int(race["race"])
        try:
            prediction = engine.predict(race, context_for(payload, race))
            if not prediction_complete(prediction):
                raise RuntimeError("prediction_output_incomplete")
            predictions[str(race_no)] = prediction
        except Exception as exc:
            failures.append({"race": race_no, "error": f"{type(exc).__name__}: {exc}"})
    if failures:
        raise RuntimeError("toda_v6_generation_failed: " + json.dumps(failures, ensure_ascii=False))
    payload["engine"] = ENGINE_ID
    payload["preds"] = predictions
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "master": "Toda_AI_MASTER_v3_1_COMPLETE_ONE_FILE",
        "generatedBy": "automation/apply_toda_v5.py",
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
        "publicSecondThirdMarginalized": True,
        "conditionalTicketChain": True,
        "raceCount": 12,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--require-open", action="store_true")
    args = parser.parse_args()
    compact = args.date.replace("-", "")
    data_root = Path(args.data_root)
    dated_path = data_root / "venues" / "toda" / f"{compact}.json"
    latest_path = data_root / "venues" / "toda" / "latest.json"
    if not dated_path.exists():
        if args.require_open:
            raise FileNotFoundError(dated_path)
        print(f"Toda data is not open: {dated_path}")
        return 0
    payload = apply_toda_v5(json.loads(dated_path.read_text(encoding="utf-8")), args.date)
    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)
    print(json.dumps({"date": args.date, "engine": payload["engine"], "raceCount": len(payload["preds"]), "datedPath": str(dated_path), "latestPath": str(latest_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
