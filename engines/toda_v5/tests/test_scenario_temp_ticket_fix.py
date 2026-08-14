import json
import sys
from pathlib import Path

ENGINE_DIR = Path(__file__).parents[1]
sys.path.insert(0, str(ENGINE_DIR))

from toda_prediction_engine_v5 import ENGINE_ID, TodaPredictionEngineV5
from toda_scenario_engine_v5 import detect_scenarios
from toda_ticket_engine_v5 import build_tickets


def context_for(payload, race):
    weather = race.get("weather") or {}
    tide = payload.get("tide") or {}
    return {
        "wind_speed": weather.get("wind_speed"),
        "wave_height": weather.get("wave_height"),
        "tide_phase": race.get("tide_phase") or tide.get("phase"),
        "tide_type": tide.get("tideType"),
    }


# Class alone must not create a head scenario when kimarite and course wins are zero.
racers = [{"lane": lane, "class": "B1", "avg_st": .16} for lane in range(1, 7)]
racers[1]["class"] = "A1"
profiles = {str(lane): {"win_rate": 0, "top3_vs_course_avg": 0, "avg_st": .16} for lane in range(1, 7)}
scores = {str(lane): 0 for lane in range(1, 7)}
scores["2"] = 3
scenarios, _ = detect_scenarios(racers, profiles, scores, {})
assert "TWO_SASHI" not in {scenario["id"] for scenario in scenarios}


# Recalculate 2026-08-08 races 3-7 from race inputs only; result/odds fields are not passed.
data_path = Path(__file__).parents[3] / "data" / "venues" / "toda" / "20260808.json"
payload = json.loads(data_path.read_text(encoding="utf-8"))
engine = TodaPredictionEngineV5()
predictions = {}
for race in payload["races"]:
    if 3 <= int(race["race"]) <= 7:
        predictions[int(race["race"])] = engine.predict({"racers": race["racers"]}, context_for(payload, race))

assert ENGINE_ID == "toda_prediction_engine_v6_20260814_marginal_conditional_ticket"
assert "TWO_SASHI" not in {scenario["id"] for scenario in predictions[3]["scenarios"]}
assert predictions[3]["win"]["2"] < 40
assert predictions[4]["win"]["4"] >= 6
assert max(predictions[5]["win"], key=predictions[5]["win"].get) == "4" and predictions[5]["win"]["4"] >= 85
assert max(predictions[6]["win"], key=predictions[6]["win"].get) == "3" and 55 <= predictions[6]["win"]["3"] <= 82
assert max(predictions[7]["win"], key=predictions[7]["win"].get) == "1" and predictions[7]["win"]["1"] >= 70
assert [predictions[r]["softmaxTemperature"] for r in range(3, 8)] == [.80, .73, .58, .73, .58]
assert all(predictions[r]["ai"] for r in range(3, 8))
assert len(predictions[6]["ai"]) == 6


# Joint ranking may retain a useful third candidate beyond the old top-two cutoff,
# while excluding a very low third probability.
win = {str(lane): (80 if lane == 1 else 4) for lane in range(1, 7)}
second = {str(lane): value for lane, value in enumerate((0, 0, 50, 30, 20, 0, 0)) if lane}
third = {str(lane): value for lane, value in enumerate((0, 0, 5, 40, 30, 20, .1)) if lane}
second_by_head = {str(head): dict(second) for head in range(1, 7)}
third_by_head = {str(head): dict(third) for head in range(1, 7)}
tickets = build_tickets(win, second_by_head, third_by_head, [{"head": 1, "links": [2, 3, 4, 5, 6]}], "S")
combos = {ticket["combo"] for ticket in tickets}
assert "1-2-5" in combos
assert all(not combo.endswith("-6") for combo in combos)

print("Toda v5 scenario/temperature/ticket regression tests passed")
