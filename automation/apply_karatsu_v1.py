from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "karatsu_v1"
sys.path.insert(0, str(ENGINE_DIR))
from karatsu_prediction_engine import KaratsuScenarioEngine, RaceInput, RacerInput, SeasonRun

ENGINE_ID = "karatsu_scenario_engine_v1_2"
LANES = (1, 2, 3, 4, 5, 6)


def number(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        parsed = float(re.sub(r"[^0-9.\-]", "", str(value)))
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def actual_entry_map(race: dict) -> dict[int, int]:
    live = race.get("live") or {}
    actual = live.get("actual_entry") or race.get("actual_entry")
    if isinstance(actual, list) and len(actual) == 6:
        return {int(lane): course for course, lane in enumerate(actual, start=1)}
    return {
        int(r.get("lane") or 0): int(r.get("actual_course") or r.get("entry_course") or r.get("lane") or 0)
        for r in race.get("racers") or []
    }


def exhibition_index(race: dict) -> dict[int, dict]:
    live = race.get("live") or {}
    entries = ((live.get("exhibition") or {}).get("entries") or live.get("exhibition_entries") or [])
    if not entries and isinstance(race.get("exhibition"), dict):
        entries = race["exhibition"].get("entries") or []
    return {int(e.get("lane") or e.get("boat_no") or 0): e for e in entries}


def original_index(race: dict) -> dict[int, dict]:
    live = race.get("live") or {}
    entries = ((live.get("original") or {}).get("entries") or live.get("original_entries") or [])
    if not entries and isinstance(race.get("original"), dict):
        entries = race["original"].get("entries") or []
    return {int(e.get("lane") or e.get("boat_no") or 0): e for e in entries}


def weather(race: dict, payload: dict) -> dict:
    live = race.get("live") or {}
    return race.get("weather") or live.get("weather") or payload.get("weather") or {}


def day_no(race: dict, payload: dict) -> int:
    return int(
        race.get("eventDay")
        or (race.get("race_meta") or {}).get("day_no")
        or payload.get("eventDay")
        or payload.get("seriesDay")
        or 1
    )


def class_state_multiplier(class_rank: str, event_day: int, motor_score: float, exhibit_score: float, season_score: float) -> tuple[float, float, float]:
    # Grade is not rewarded alone. It amplifies only verified current form.
    early = event_day <= 2
    late = event_day >= 5
    state = (motor_score + exhibit_score) / 2 if early else (season_score if late else (motor_score + exhibit_score + season_score) / 3)
    positive = max(0.0, min(1.0, (state + 1.0) / 2.0))
    negative = max(0.0, min(1.0, (-state + 1.0) / 2.0))
    if class_rank == "A1":
        return (1.0 + 0.03 * positive - 0.02 * negative, 1.0 + 0.08 * positive - 0.03 * negative, 1.0 + 0.08 * positive - 0.03 * negative)
    if class_rank == "A2":
        return (1.0 + 0.02 * positive - 0.02 * negative, 1.0 + 0.05 * positive - 0.03 * negative, 1.0 + 0.05 * positive - 0.03 * negative)
    if class_rank == "B2":
        return (0.94 + 0.03 * positive - 0.04 * negative, 0.94 + 0.06 * positive - 0.05 * negative, 0.96 + 0.08 * positive - 0.04 * negative)
    return (1.0, 1.0, 1.0)


def season_runs(racer: dict) -> list[SeasonRun]:
    rows = racer.get("season_runs") or []
    converted: list[SeasonRun] = []

    # Source data is generally oldest -> newest. v1.2 expects newest first.
    for run in reversed(rows[-5:]):
        finish_match = re.search(r"[1-6]", str(run.get("finish") or "6"))
        finish = int(finish_match.group()) if finish_match else 6
        course = int(run.get("entry_course") or run.get("course") or 6)
        st_value = run.get("st")
        converted.append(
            SeasonRun(
                finish=finish,
                course=course,
                st=number(st_value, None),
            )
        )
    return converted


def motor_score(racer: dict) -> float:
    return max(-1.0, min(1.0, ((number(racer.get("motor_3"), 50) - 50) / 18 + (number(racer.get("boat_3"), 50) - 50) / 25) / 2))


def exhibit_score(ex: dict, original: dict) -> float:
    rank = number(ex.get("exhibition_rank"), 3.5)
    rank_part = (3.5 - rank) / 2.5
    lap = number(original.get("lap_time"), 0)
    turn = number(original.get("turn_time"), 0)
    original_part = 0.0
    if lap:
        original_part += 0.25
    if turn:
        original_part += 0.25
    return max(-1.0, min(1.0, rank_part * 0.75 + original_part))


def normalize(values: dict[int, float]) -> dict[int, float]:
    total = sum(max(0.0, v) for v in values.values()) or 1.0
    return {k: max(0.0, v) / total for k, v in values.items()}



def same_day_water_bias(payload: dict, race_no: int) -> dict[int, float]:
    """Build a small course bias from completed earlier races only.

    The correction is capped at ±0.06 and never uses odds.
    It starts after 3R and is intentionally weaker than player/course/form inputs.
    """
    if race_no <= 3:
        return {}

    scores = {lane: 0.0 for lane in LANES}
    counts = {lane: 0 for lane in LANES}

    for prior in payload.get("races") or []:
        prior_no = int(prior.get("race") or 0)
        if prior_no <= 0 or prior_no >= race_no:
            continue

        result = prior.get("result") or {}
        order = result.get("order") or []
        if not isinstance(order, list) or len(order) < 3:
            continue

        for pos, lane_value in enumerate(order[:3], start=1):
            try:
                lane = int(lane_value)
            except (TypeError, ValueError):
                continue
            scores[lane] += {1: 1.0, 2: 0.45, 3: 0.20}[pos]
            counts[lane] += 1

    if not any(counts.values()):
        return {}

    average = sum(scores.values()) / 6.0
    bias = {}
    for lane in LANES:
        raw = (scores[lane] - average) * 0.018
        bias[lane] = max(-0.06, min(0.06, raw))
    return bias


def build_race_input(payload: dict, race: dict) -> tuple[RaceInput, dict[int, dict], dict[int, dict]]:
    entry = actual_entry_map(race)
    ex_map = exhibition_index(race)
    org_map = original_index(race)
    racers = []
    for r in race.get("racers") or []:
        lane = int(r["lane"])
        ex = ex_map.get(lane, {})
        org = org_map.get(lane, {})
        racers.append(RacerInput(
            lane=lane,
            actual_course=int(entry.get(lane, lane)),
            class_rank=str(r.get("class") or "B1"),
            nat_win=number(r.get("nat_win")),
            nat_top3=number(r.get("nat_3")),
            local_win=number(r.get("local_win")),
            local_top3=number(r.get("local_3")),
            avg_st=number(r.get("avg_st"), 0.18),
            motor_2=number(r.get("motor_2"), 33),
            motor_3=number(r.get("motor_3"), 50),
            boat_2=number(r.get("boat_2"), 33),
            boat_3=number(r.get("boat_3"), 50),
            exhibition_time=number(ex.get("exhibition_time"), None),
            exhibition_st=number(ex.get("start_time"), None),
            lap_time=number(org.get("lap_time"), None),
            turn_time=number(org.get("turn_time"), None),
            straight_time=number(org.get("straight_time"), None),
            season_score=None,
            season_runs=season_runs(r),
            tilt=number(ex.get("tilt"), number(r.get("tilt"), 0)),
            withdrawn=bool(r.get("withdrawn", False)),
        ))
    w = weather(race, payload)
    race_no = int(race.get("race") or (race.get("race_meta") or {}).get("race_no") or 0)
    return RaceInput(
        racers=racers,
        wind_speed=number(w.get("wind_speed") or w.get("wind"), 0),
        wave_height=number(w.get("wave_height") or w.get("wave"), 0),
        tide_phase=str((race.get("tide") or {}).get("phase") or "unknown"),
        day_no=day_no(race, payload),
        same_day_water_bias=same_day_water_bias(payload, race_no),
    ), ex_map, org_map


def site_prediction(payload: dict, race: dict) -> dict:
    race_input, ex_map, org_map = build_race_input(payload, race)
    prediction = KaratsuScenarioEngine().predict(race_input, ticket_count=10)
    first, second, third = dict(prediction.marginal_first), dict(prediction.marginal_second), dict(prediction.marginal_third)
    state_diag = [
        {
            "lane": int(r["lane"]),
            "class": r.get("class"),
            "seasonRuns": [
                {"finish": x.finish, "course": x.course, "st": x.st}
                for x in season_runs(r)
            ],
        }
        for r in (race.get("racers") or [])
    ]
    top3 = {lane: first[lane] + second[lane] + third[lane] for lane in LANES}
    entry = [r.lane for r in sorted(race_input.racers, key=lambda x: x.actual_course)]
    tickets = []
    for idx, combo in enumerate(prediction.tickets):
        role = "本線" if idx < 6 else ("ズレ対応" if idx < 8 else "荒れ対応")
        tickets.append({"combo": combo, "role": role, "odds": "-"})
    scenarios = [{"name": s.name, "probability": round(s.weight * 100, 1), "attackCourse": s.attack_course, "notes": s.notes} for s in prediction.scenarios]
    ranked = sorted(first, key=first.get, reverse=True)
    return {
        "status": "complete",
        "engine": ENGINE_ID,
        "engineVersion": "1.2.0",
        "win": {str(k): round(v * 100, 1) for k, v in first.items()},
        "second": {str(k): round(v * 100, 1) for k, v in second.items()},
        "third": {str(k): round(v * 100, 1) for k, v in third.items()},
        "top3": {str(k): round(v * 100, 1) for k, v in top3.items()},
        "sab": prediction.sab,
        "confidence": round((first[ranked[0]] - first[ranked[1]]) * 100 + 50, 1),
        "actualEntry": entry,
        "entryChanged": entry != [1, 2, 3, 4, 5, 6],
        "dayStage": "early" if race_input.day_no <= 2 else ("late" if race_input.day_no >= 5 else "middle"),
        "weights": {"early": {"motorBoat": 0.24, "exhibition": 0.20, "playerCourse": 0.29, "coursePrior": 0.17, "season": 0.10}, "middle": {"motorBoat": 0.19, "exhibition": 0.17, "playerCourse": 0.24, "coursePrior": 0.17, "season": 0.23}, "late": {"motorBoat": 0.14, "exhibition": 0.13, "playerCourse": 0.21, "coursePrior": 0.17, "season": 0.35}},
        "ai": tickets[:8],
        "aiUpset": tickets[8:10],
        "scenarios": scenarios,
        "readability": {"axisLane": ranked[0], "secondHeadLane": ranked[1], "axisGap": round((first[ranked[0]] - first[ranked[1]]) * 100, 1)},
        "trifectaTop20": [
            {"combo": combo, "probability": round(prob * 100, 4)}
            for combo, prob in list(prediction.trifecta_probabilities.items())[:20]
        ],
        "diagnostics": {
            "classState": state_diag,
            "sameDayWaterBias": race_input.same_day_water_bias,
            "oddsUsedForPrediction": False,
            "engineDiagnostics": prediction.diagnostics,
        },
    }


def apply_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("venueId") != "karatsu" and payload.get("venue") != "唐津":
        raise RuntimeError("not_karatsu_payload")
    races = payload.get("races") or []
    if len(races) != 12:
        raise RuntimeError("karatsu_races_must_be_12")
    predictions = {}
    for race in races:
        prediction = site_prediction(payload, race)
        race["prediction"] = prediction
        race_no = int(race.get("race") or (race.get("race_meta") or {}).get("race_no") or 0)
        if race_no:
            predictions[str(race_no)] = prediction

    # Keep the top-level prediction domains synchronized with the race-level output.
    # The morning three-stage pipeline validates and preserves these fields.
    payload["engine"] = ENGINE_ID
    payload["engineVersion"] = "1.2.0"
    payload["preds"] = predictions
    payload["predictionStatus"] = "ready"
    payload["predictionReason"] = ""

    atomic_write_json(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYYMMDD or YYYY-MM-DD")
    args = parser.parse_args()

    normalized_date = re.sub(r"[^0-9]", "", args.date)
    if len(normalized_date) != 8:
        raise ValueError(f"invalid_date:{args.date}")

    path = REPO_ROOT / "data" / "venues" / "karatsu" / f"{normalized_date}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    apply_file(path)
    latest = path.parent / "latest.json"
    atomic_write_json(latest, json.loads(path.read_text(encoding="utf-8")))
    print(path)


if __name__ == "__main__":
    main()
