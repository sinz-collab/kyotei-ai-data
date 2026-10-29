from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from pathlib import Path

from naruto_v5_engine import NarutoPrediction, NarutoV5Engine


HERE = Path(__file__).resolve().parent
DEFAULT_ARTIFACT = HERE / "models" / "naruto_v5_fixed_20261020.joblib"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def normalize_racers(rows: list[dict], direct: dict | None = None) -> list[dict]:
    direct_by_lane = {
        int(row.get("lane") or 0): row
        for row in ((direct or {}).get("data", {}).get("racers") or [])
    }
    normalized = []
    for source in rows:
        lane = int(source.get("lane") or 0)
        live = direct_by_lane.get(lane, {})
        normalized.append(
            {
                "lane": lane,
                "player_id": source.get("player_id") or live.get("player_id") or 0,
                "player_name": source.get("name") or live.get("name"),
                "class": source.get("class"),
                "national_win_rate": source.get("national_win_rate", source.get("nat_win")),
                "national_2rate_pct": source.get("national_2rate_pct", source.get("nat_2")),
                "local_win_rate": source.get("local_win_rate", source.get("local_win")),
                "local_2rate_pct": source.get("local_2rate_pct", source.get("local_2")),
                "motor_2rate_pct": source.get("motor_2rate_pct", source.get("motor_2")),
                "boat_2rate_pct": source.get("boat_2rate_pct", source.get("boat_2")),
            }
        )
    return normalized


def live_entries(exhibition: dict, original: dict) -> list[dict]:
    exhibition_by_lane = {
        int(row.get("lane") or 0): row
        for row in exhibition.get("data", {}).get("entries") or []
    }
    original_by_lane = {
        int(row.get("lane") or 0): row
        for row in original.get("data", {}).get("entries") or []
    }
    return [
        {
            "lane": lane,
            "exhibition_st": exhibition_by_lane.get(lane, {}).get("start_time"),
            "exhibition_course": exhibition_by_lane.get(lane, {}).get("exhibition_course"),
            "exhibition_time": exhibition_by_lane.get(lane, {}).get("exhibition_time"),
            "lap_time": original_by_lane.get(lane, {}).get("lap_time"),
            "turn_time": original_by_lane.get(lane, {}).get("turn_time"),
            "straight_time": original_by_lane.get(lane, {}).get("straight_time"),
        }
        for lane in range(1, 7)
    ]


def percent(value: float) -> float:
    return round(100 * float(value), 6)


def prediction_payload(prediction: NarutoPrediction) -> dict:
    normal = [
        {**ticket, "probability": percent(ticket["probability"]), "role": "通常"}
        for ticket in prediction.normal_tickets
    ]
    fly = [
        {**ticket, "probability": percent(ticket["probability"]), "role": "飛び"}
        for ticket in prediction.fly_tickets
    ]
    return {
        "phase": "preliminary" if prediction.stage == "morning" else "final",
        "predictionStage": prediction.stage,
        "modelVersion": prediction.model_version,
        "generatedAt": prediction.generated_at,
        "win": {str(lane): percent(value) for lane, value in enumerate(prediction.first, 1)},
        "second": {str(lane): percent(value) for lane, value in enumerate(prediction.second, 1)},
        "third": {str(lane): percent(value) for lane, value in enumerate(prediction.third, 1)},
        "tickets": normal,
        "ai": normal,
        "aiUpset": [],
        "lane1FlyProbability": percent(prediction.fly_probability),
        "flyPrediction": {
            "status": "complete",
            "stage": prediction.stage,
            "modelVersion": prediction.model_version,
            "probability": percent(prediction.fly_probability),
            "tickets": fly,
            "allocation": [6, 2, 1, 1],
            "overlapPolicy": "allowed",
        },
        "trifectaProbabilities": [
            {**row, "probability": percent(row["probability"])}
            for row in prediction.probabilities
        ],
        "inputSnapshot": prediction.input_snapshot,
        "oddsUsedForProbability": False,
    }


def append_history(previous: dict | None, current: dict, stage: str) -> dict:
    history = deepcopy((previous or {}).get("prediction_history") or {"morning": [], "live": []})
    history.setdefault("morning", [])
    history.setdefault("live", [])
    snapshot = {
        "generated_at": current["generatedAt"],
        "model_version": current["modelVersion"],
        "stage": stage,
        "input": deepcopy(current["inputSnapshot"]),
        "prediction": deepcopy(current),
    }
    snapshot["prediction"].pop("prediction_history", None)
    if not any(item.get("generated_at") == snapshot["generated_at"] for item in history[stage]):
        history[stage].append(snapshot)
    current["prediction_history"] = history
    current["active_prediction_stage"] = stage
    return current


def unavailable_live(previous: dict, reason: str) -> dict:
    current = deepcopy(previous)
    current["liveStatus"] = "unavailable"
    current["liveReason"] = reason
    current["flyPrediction"] = {
        "status": "unavailable",
        "stage": "live",
        "modelVersion": "naruto_v5_live_alpha050_6211_fixed_20261020",
        "tickets": [],
        "reason": reason,
    }
    return current


def result_evaluation(prediction: dict, result_document: dict) -> dict:
    data = result_document.get("data") or {}
    order = data.get("order") or []
    combo = "-".join(str(value) for value in order[:3])
    if not re.fullmatch(r"[1-6]-[1-6]-[1-6]", combo):
        raise ValueError("naruto_result_combination_missing")
    payout_text = str(data.get("payout3t") or "")
    payout_digits = re.sub(r"\D", "", payout_text)
    if not payout_digits:
        raise ValueError("naruto_result_payout_missing")
    payout = int(payout_digits)
    normal = [str(ticket.get("combo")) for ticket in prediction.get("ai") or []]
    fly = [str(ticket.get("combo")) for ticket in (prediction.get("flyPrediction") or {}).get("tickets") or []]
    combined = list(dict.fromkeys(normal + fly))

    def score(tickets: list[str]) -> dict:
        hit = combo in tickets
        stake = 100 * len(tickets)
        returned = payout if hit else 0
        return {
            "ticket_count": len(tickets),
            "stake_yen": stake,
            "hit": hit,
            "return_yen": returned,
            "return_rate_pct": round(100 * returned / stake, 2) if stake else 0.0,
        }

    return {
        "status": "complete",
        "result": combo,
        "payout_yen": payout,
        "normal": score(normal),
        "fly": score(fly),
        "combined_unique": score(combined),
        "normal_and_fly_hit": combo in normal and combo in fly,
        "evaluated_at": result_document.get("fetched_at"),
    }


def run_result(payload: dict, race_no: int, live_root: Path) -> dict:
    result_document = read_json(live_root / "result.json")
    if result_document.get("complete") is not True:
        raise ValueError("naruto_result_not_complete")
    race = next(row for row in payload.get("races") or [] if int(row.get("race") or 0) == race_no)
    current = deepcopy((payload.get("preds") or {}).get(str(race_no)) or race.get("prediction") or {})
    if not current.get("prediction_history"):
        raise ValueError("naruto_prediction_history_missing")
    evaluation = result_evaluation(current, result_document)
    current["resultEvaluation"] = evaluation
    history = current["prediction_history"]
    for stage in ("morning", "live"):
        for snapshot in history.get(stage) or []:
            snapshot["result_evaluation"] = result_evaluation(snapshot.get("prediction") or {}, result_document)
    payload.setdefault("preds", {})[str(race_no)] = current
    race["prediction"] = current
    race["result"] = deepcopy(result_document.get("data") or {})
    return payload


def update_first60_metrics(data_root: Path, date: str, race_no: int, prediction: dict) -> dict:
    path = data_root / "venues" / "naruto" / "v5_first60_metrics.json"
    document = read_json(path) if path.is_file() else {
        "model_version": "naruto_v5_live_alpha050_6211_fixed_20261020",
        "limit": 60,
        "frozen": True,
        "races": [],
    }
    evaluation = prediction.get("resultEvaluation") or {}
    if evaluation.get("status") != "complete":
        raise ValueError("naruto_result_evaluation_missing")
    key = f"{date}-{race_no:02d}"
    existing = next((row for row in document["races"] if row.get("key") == key), None)
    record = {
        "key": key,
        "date": date,
        "race": race_no,
        "model_version": prediction.get("modelVersion"),
        "stage": prediction.get("active_prediction_stage"),
        "generated_at": prediction.get("generatedAt"),
        "fly_probability_pct": prediction.get("lane1FlyProbability"),
        "actual_fly": not str(evaluation["result"]).startswith("1-"),
        "result_evaluation": deepcopy(evaluation),
    }
    if existing:
        if existing != record:
            raise ValueError(f"naruto_first60_immutable_conflict:{key}")
    elif len(document["races"]) < int(document.get("limit") or 60):
        document["races"].append(record)

    rows = document["races"]
    summary: dict[str, object] = {"race_count": len(rows)}
    for name in ("normal", "fly", "combined_unique"):
        scores = [row["result_evaluation"][name] for row in rows]
        stake = sum(int(score["stake_yen"]) for score in scores)
        returned = sum(int(score["return_yen"]) for score in scores)
        hits = sum(bool(score["hit"]) for score in scores)
        summary[name] = {
            "hits": hits,
            "hit_rate_pct": round(100 * hits / len(scores), 2) if scores else 0.0,
            "stake_yen": stake,
            "return_yen": returned,
            "return_rate_pct": round(100 * returned / stake, 2) if stake else 0.0,
        }
    if rows:
        predicted = [float(row["fly_probability_pct"]) / 100 for row in rows]
        actual = [int(row["actual_fly"]) for row in rows]
        summary["fly_probability"] = {
            "mean_predicted_pct": round(100 * sum(predicted) / len(rows), 2),
            "actual_rate_pct": round(100 * sum(actual) / len(rows), 2),
            "brier_score": round(sum((p - y) ** 2 for p, y in zip(predicted, actual)) / len(rows), 6),
        }
    document["summary"] = summary
    write_json_atomic(path, document)
    return document


def run_morning(engine: NarutoV5Engine, payload: dict) -> dict:
    previous_preds = payload.get("preds") or {}
    payload["preds"] = dict(previous_preds)
    for race in payload.get("races") or []:
        race_no = int(race["race"])
        previous = previous_preds.get(str(race_no))
        predicted = engine.predict_morning(
            date=payload["date"], race_no=race_no, racers=normalize_racers(race.get("racers") or [])
        )
        current = append_history(previous, prediction_payload(predicted), "morning")
        payload["preds"][str(race_no)] = current
        race["prediction"] = current
        race["predictionPre"] = current
    payload["engine"] = "naruto_v5"
    payload["engineVersion"] = "naruto_v5_live_alpha050_6211_fixed_20261020"
    return payload


def run_live(engine: NarutoV5Engine, payload: dict, race_no: int, live_root: Path) -> dict:
    race = next(row for row in payload.get("races") or [] if int(row.get("race") or 0) == race_no)
    previous = (payload.get("preds") or {}).get(str(race_no)) or race.get("prediction") or {}
    direct = read_json(live_root / "direct.json")
    exhibition = read_json(live_root / "exhibition.json")
    original = read_json(live_root / "original_exhibition.json")
    required = {"direct": direct, "exhibition": exhibition, "original_exhibition": original}
    incomplete = [name for name, document in required.items() if document.get("complete") is not True]
    actual_entry = direct.get("data", {}).get("actual_entry") or []
    if incomplete:
        current = unavailable_live(previous, "missing:" + ",".join(incomplete))
    elif direct.get("data", {}).get("entry_changed") is True or actual_entry != [1, 2, 3, 4, 5, 6]:
        current = unavailable_live(previous, "entry_change_unverified")
    else:
        predicted = engine.predict_live(
            date=payload["date"],
            race_no=race_no,
            racers=normalize_racers(race.get("racers") or [], direct),
            live_entries=live_entries(exhibition, original),
            wind_speed_mps=direct.get("data", {}).get("wind_speed"),
            wave_height_cm=direct.get("data", {}).get("wave_height"),
        )
        current = append_history(previous, prediction_payload(predicted), "live")
        current["realtime"] = {
            "direct": direct.get("data"),
            "exhibition": exhibition.get("data"),
            "original_exhibition": original.get("data"),
        }
    payload.setdefault("preds", {})[str(race_no)] = current
    race["prediction"] = current
    race["predictionFinal"] = current
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--stage", choices=("morning", "live", "result"), required=True)
    parser.add_argument("--race", type=int)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--live-root", type=Path)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    path = args.data_root / "venues" / "naruto" / f"{args.date.replace('-', '')}.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = read_json(path)
    if payload.get("venueId") != "naruto" or payload.get("date") != args.date:
        raise RuntimeError("naruto_payload_identity_mismatch")
    engine = NarutoV5Engine(args.artifact)
    if args.stage == "morning":
        payload = run_morning(engine, payload)
    elif args.stage == "live":
        if args.race not in range(1, 13) or args.live_root is None:
            raise ValueError("live stage requires --race 1..12 and --live-root")
        payload = run_live(engine, payload, args.race, args.live_root)
    else:
        if args.race not in range(1, 13) or args.live_root is None:
            raise ValueError("result stage requires --race 1..12 and --live-root")
        payload = run_result(payload, args.race, args.live_root)
        update_first60_metrics(args.data_root, args.date, args.race, payload["preds"][str(args.race)])
    serialized = deepcopy(payload)
    write_json_atomic(path, serialized)
    write_json_atomic(path.parent / "latest.json", serialized)
    print(json.dumps({"date": args.date, "stage": args.stage, "race": args.race, "written": str(path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
