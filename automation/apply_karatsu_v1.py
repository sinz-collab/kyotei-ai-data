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
from karatsu_prediction_engine import KaratsuScenarioEngine, RaceInput, RacerInput

ENGINE_ID = "karatsu_scenario_engine_v1_0"
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


def season_score(racer: dict) -> float:
    runs = racer.get("season_runs") or []
    if not runs:
        return 0.0
    total = 0.0
    weight = 0.0
    for i, run in enumerate(runs[-10:]):
        w = 1.0 + i * 0.08
        finish = int(re.search(r"[1-6]", str(run.get("finish") or "6")).group()) if re.search(r"[1-6]", str(run.get("finish") or "6")) else 6
        total += w * {1: 1.0, 2: 0.65, 3: 0.35, 4: 0.0, 5: -0.4, 6: -0.7}[finish]
        weight += w
    return max(-1.0, min(1.0, total / max(weight, 1.0)))


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


def apply_position_multipliers(prediction, racers: list[dict], event_day: int, ex_map: dict[int, dict], org_map: dict[int, dict]):
    first, second, third = dict(prediction.marginal_first), dict(prediction.marginal_second), dict(prediction.marginal_third)
    diagnostics = []
    for racer in racers:
        lane = int(racer["lane"])
        m = motor_score(racer)
        e = exhibit_score(ex_map.get(lane, {}), org_map.get(lane, {}))
        s = season_score(racer)
        f1, f2, f3 = class_state_multiplier(str(racer.get("class") or "B1"), event_day, m, e, s)
        first[lane] *= f1
        second[lane] *= f2
        third[lane] *= f3
        diagnostics.append({"lane": lane, "class": racer.get("class"), "motor_state": round(m, 3), "exhibition_state": round(e, 3), "season_state": round(s, 3), "position_multiplier": [round(f1, 3), round(f2, 3), round(f3, 3)]})
    return normalize(first), normalize(second), normalize(third), diagnostics


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
            tilt=number(ex.get("tilt"), number(r.get("tilt"), 0)),
            withdrawn=bool(r.get("withdrawn", False)),
        ))
    w = weather(race, payload)
    return RaceInput(racers=racers, wind_speed=number(w.get("wind_speed") or w.get("wind"), 0), wave_height=number(w.get("wave_height") or w.get("wave"), 0), tide_phase=str((race.get("tide") or {}).get("phase") or "unknown"), day_no=day_no(race, payload)), ex_map, org_map


def site_prediction(payload: dict, race: dict) -> dict:
    race_input, ex_map, org_map = build_race_input(payload, race)
    prediction = KaratsuScenarioEngine().predict(race_input, ticket_count=10)
    first, second, third, state_diag = apply_position_multipliers(prediction, race.get("racers") or [], race_input.day_no, ex_map, org_map)
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
        "engineVersion": "1.0",
        "win": {str(k): round(v * 100, 1) for k, v in first.items()},
        "second": {str(k): round(v * 100, 1) for k, v in second.items()},
        "third": {str(k): round(v * 100, 1) for k, v in third.items()},
        "top3": {str(k): round(v * 100, 1) for k, v in top3.items()},
        "sab": prediction.sab,
        "confidence": round((first[ranked[0]] - first[ranked[1]]) * 100 + 50, 1),
        "actualEntry": entry,
        "entryChanged": entry != [1, 2, 3, 4, 5, 6],
        "dayStage": "early" if race_input.day_no <= 2 else ("late" if race_input.day_no >= 5 else "middle"),
        "weights": {"early": {"motorBoat": 0.30, "exhibition": 0.25, "playerCourse": 0.25, "waterScenario": 0.15, "season": 0.05}, "middle": {"motorBoat": 0.22, "exhibition": 0.20, "playerCourse": 0.22, "waterScenario": 0.15, "season": 0.21}, "late": {"motorBoat": 0.15, "exhibition": 0.15, "playerCourse": 0.20, "waterScenario": 0.15, "season": 0.35}},
        "ai": tickets[:8],
        "aiUpset": tickets[8:10],
        "scenarios": scenarios,
        "readability": {"axisLane": ranked[0], "secondHeadLane": ranked[1], "axisGap": round((first[ranked[0]] - first[ranked[1]]) * 100, 1)},
        "diagnostics": {"classState": state_diag, "oddsUsedForPrediction": False, "engineDiagnostics": prediction.diagnostics},
    }


def apply_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("venueId") != "karatsu" and payload.get("venue") != "唐津":
        raise RuntimeError("not_karatsu_payload")
    races = payload.get("races") or []
    if len(races) != 12:
        raise RuntimeError("karatsu_races_must_be_12")
    for race in races:
        race["prediction"] = site_prediction(payload, race)
    atomic_write_json(path, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYYMMDD")
    args = parser.parse_args()
    path = REPO_ROOT / "data" / "venues" / "karatsu" / f"{args.date}.json"
    if not path.exists():
        raise FileNotFoundError(path)
    apply_file(path)
    latest = path.parent / "latest.json"
    atomic_write_json(latest, json.loads(path.read_text(encoding="utf-8")))
    print(path)


if __name__ == "__main__":
    main()
