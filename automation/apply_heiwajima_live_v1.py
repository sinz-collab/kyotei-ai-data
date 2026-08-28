from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "heiwajima_v1"
AUTOMATION_DIR = REPO_ROOT / "automation"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))
if str(AUTOMATION_DIR) not in sys.path:
    sys.path.insert(0, str(AUTOMATION_DIR))

from heiwajima_live_review import apply_live_update
from heiwajima_prediction_engine import calculate
from apply_heiwajima_v1 import (
    ENGINE_ID,
    atomic_write_json,
    build_player_index,
    engine_input_for,
    site_prediction,
)


def load_document(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def valid_complete(document: dict | None) -> bool:
    return bool(document and document.get("complete") is True and document.get("status") == "complete")


def relative_scores(
    rows: list[dict],
    key: str,
    *,
    lower_is_better: bool,
) -> dict[int, float]:
    values: list[tuple[int, float]] = []
    for row in rows:
        lane = row.get("lane")
        raw = row.get(key)
        if lane is None or raw in (None, "", "-"):
            continue
        try:
            values.append((int(lane), float(raw)))
        except (TypeError, ValueError):
            continue

    if len(values) < 2:
        return {}

    raw_values = [value for _, value in values]
    mean = sum(raw_values) / len(raw_values)
    variance = sum((value - mean) ** 2 for value in raw_values) / len(raw_values)
    std = variance ** 0.5
    if std <= 1e-9:
        return {lane: 0.0 for lane, _ in values}

    result: dict[int, float] = {}
    for lane, value in values:
        z = (value - mean) / std
        if lower_is_better:
            z = -z
        result[lane] = max(-2.0, min(2.0, z))
    return result


def live_input(live_root: Path) -> tuple[dict, list[str]]:
    direct = load_document(live_root / "direct.json")
    exhibition = load_document(live_root / "exhibition.json")
    original = load_document(live_root / "original_exhibition.json")
    odds = load_document(live_root / "odds.json")
    missing: list[str] = []
    if not valid_complete(direct):
        missing.append("direct_data_pending")
    if not valid_complete(exhibition):
        missing.append("exhibition_pending")
    if not valid_complete(original):
        missing.append("original_exhibition_pending")

    direct_data = (direct or {}).get("data") or {}
    exhibition_rows = ((exhibition or {}).get("data") or {}).get("entries") or []
    original_rows = ((original or {}).get("data") or {}).get("entries") or []
    direct_rows = direct_data.get("racers") or []
    direct_by_lane = {int(row.get("lane")): row for row in direct_rows if row.get("lane")}
    original_by_lane = {int(row.get("lane")): row for row in original_rows if row.get("lane")}

    straight_scores = relative_scores(
        original_rows,
        "straight_time",
        lower_is_better=True,
    )
    turn_scores = relative_scores(
        original_rows,
        "turn_time",
        lower_is_better=True,
    )
    sum_scores = relative_scores(
        original_rows,
        "sum_difference",
        lower_is_better=False,
    )
    lap_scores = relative_scores(
        original_rows,
        "lap_time",
        lower_is_better=True,
    )

    entries = []
    exhibitions = []
    for row in exhibition_rows:
        boat_no = int(row.get("lane"))
        actual_course = int(row.get("exhibition_course") or boat_no)
        entries.append({"boat_no": boat_no, "actual_course": actual_course})
        original_row = original_by_lane.get(boat_no, {})
        direct_row = direct_by_lane.get(boat_no, {})
        exhibitions.append(
            {
                "boat_no": boat_no,
                # ST is intentionally a very small standalone correction in the engine.
                "st_delta": float(str(row.get("start_time") or "0").replace("F", "-0") or 0),
                "lap_score": lap_scores.get(boat_no, 0.0),
                "straight_score": straight_scores.get(boat_no, 0.0),
                "turn_score": turn_scores.get(boat_no, 0.0),
                "sum_score": sum_scores.get(boat_no, 0.0),
                "straight_time": original_row.get("straight_time"),
                "turn_time": original_row.get("turn_time"),
                "lap_time": original_row.get("lap_time"),
                "sum_difference": original_row.get("sum_difference"),
                "exhibition_time": row.get("exhibition_time"),
                "tilt": row.get("tilt") or direct_row.get("tilt"),
                "weight": direct_row.get("weight"),
                "weight_adjustment": direct_row.get("weight_adjustment"),
                "parts_exchange": direct_row.get("parts_exchange"),
            }
        )
    exhibition_st = {
        str(int(row.get("lane"))): float(str(row.get("start_time") or "0").replace("F", "-0") or 0)
        for row in exhibition_rows if row.get("lane")
    }
    st_values = sorted(exhibition_st.values())
    slit = {}
    if len(st_values) == 6:
        second_fastest = st_values[1]
        median_st = (st_values[2] + st_values[3]) / 2.0
        for lane in range(1, 7):
            st = exhibition_st.get(str(lane), 0.99)
            if st <= second_fastest + 0.02:
                slit[str(lane)] = "advance"
            elif st >= median_st + 0.08:
                slit[str(lane)] = "dent"
            else:
                slit[str(lane)] = "neutral"

    straight_pairs = []
    for row in original_rows:
        if row.get("lane") and row.get("straight_time") not in (None, "", "-"):
            try:
                straight_pairs.append((int(row["lane"]), float(row["straight_time"])))
            except (TypeError, ValueError):
                pass
    straight_pairs.sort(key=lambda x: x[1])
    straight_rank = {str(lane): rank for rank, (lane, _) in enumerate(straight_pairs, start=1)}

    live = {
        "entries": entries,
        "exhibitions": exhibitions,
        "slit": slit,
        "exhibition_st": exhibition_st,
        "straight_rank": straight_rank,
        "weather": {
            "weather": direct_data.get("weather"),
            "wind_direction": direct_data.get("wind_direction"),
            "wind_speed": direct_data.get("wind_speed"),
            "wave_height": direct_data.get("wave_height"),
            "water_temperature": direct_data.get("water_temperature"),
        },
        "stabilizer": direct_data.get("stabilizer"),
        "shortened_laps": direct_data.get("lap_shortened"),
    }
    if valid_complete(odds):
        live["odds"] = ((odds or {}).get("data") or {}).get("odds") or {}
    return live, missing


def apply_live_review(payload: dict, race_no: int, live_root: Path) -> dict:
    race = next((item for item in payload.get("races") or [] if int(item.get("race") or 0) == race_no), None)
    if race is None:
        raise RuntimeError(f"race_not_found: {race_no}")
    player_index = build_player_index()
    pre_input, connector_missing = engine_input_for(payload, race, player_index, stage="pre")
    live, live_missing = live_input(live_root)
    if not live.get("entries"):
        raise RuntimeError("complete_exhibition_entries_required_for_live_review")
    final_input = apply_live_update(pre_input, live)
    result = calculate(final_input)
    prediction = site_prediction(result, connector_missing + live_missing)

    previous = (payload.get("preds") or {}).get(str(race_no)) or {}
    prediction["probabilityReview"] = {}
    for lane in range(1, 7):
        key = str(lane)
        prediction["probabilityReview"][key] = {
            "morningWin": (previous.get("win") or {}).get(key),
            "morningSecond": (previous.get("second") or {}).get(key),
            "morningThird": (previous.get("third") or {}).get(key),
            "win": prediction["win"][key],
            "second": prediction["second"][key],
            "third": prediction["third"][key],
            "deltaWin": round(prediction["win"][key] - float((previous.get("win") or {}).get(key, prediction["win"][key])), 1),
            "deltaSecond": round(prediction["second"][key] - float((previous.get("second") or {}).get(key, prediction["second"][key])), 1),
            "deltaThird": round(prediction["third"][key] - float((previous.get("third") or {}).get(key, prediction["third"][key])), 1),
        }
    prediction["probabilityReviewStatus"] = "reviewed"
    prediction["probabilityFlow"] = {
        "required": True,
        "baseApplied": True,
        "baseLabel": "平和島v1事前エンジン予想",
        "realtimeApplied": True,
        "realtimeLabel": "展示・実進入・直前情報反映",
        "reviewed": True,
        "reviewLabel": "再精査後の調整数字",
        "adjustedRequired": True,
    }
    prediction["predictionStage"] = {
        "label": "本予想",
        "statusText": "直前情報と実進入をサーバー側で反映して再精査済み",
        "badge": "本予想",
        "color": "green",
    }
    prediction["odds"] = live.get("odds") or previous.get("odds") or {}
    display_odds = prediction["odds"]
    for ticket in prediction.get("ai") or []:
        combo = str(ticket.get("combo") or "")
        ticket["odds"] = display_odds.get(combo, "-")
    for ticket in prediction.get("aiUpset") or []:
        combo = str(ticket.get("combo") or "")
        ticket["odds"] = display_odds.get(combo, "-")

    prediction["liveReviewMeta"] = {
        "method": "heiwajima_server_live_review_v1",
        "actualEntryReanalysis": True,
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
    }
    payload.setdefault("preds", {})[str(race_no)] = prediction
    payload["engine"] = ENGINE_ID
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--live-root", default=None)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    date_key = args.date.replace("-", "")
    dated_path = data_root / "venues" / "heiwajima" / f"{date_key}.json"
    latest_path = data_root / "venues" / "heiwajima" / "latest.json"
    live_root = Path(args.live_root) if args.live_root else data_root / "live" / args.date / "heiwajima" / f"{args.race:02d}"
    if not dated_path.exists():
        raise FileNotFoundError(dated_path)
    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    payload = apply_live_review(payload, args.race, live_root)
    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)
    print(json.dumps({"date": args.date, "race": args.race, "engine": ENGINE_ID}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
