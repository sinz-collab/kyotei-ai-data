import json
from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_live_review_v5 import apply_live_review
from toda_fly_prediction_v5 import build_fly_prediction
from toda_prediction_engine_v5 import TodaPredictionEngineV5


RACE_COUNTS = {"2026-09-26": 12, "2026-09-27": 12, "2026-09-28": 7}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _context(payload, race):
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


def _replay():
    predictions = {}
    for date, race_count in RACE_COUNTS.items():
        payload = _load(REPO_ROOT / "data" / "venues" / "toda" / (date.replace("-", "") + ".json"))
        races = {int(race["race"]): race for race in payload["races"]}
        for race_no in range(1, race_count + 1):
            race = races[race_no]
            prediction = TodaPredictionEngineV5().predict(race, _context(payload, race))
            live_root = REPO_ROOT / "data" / "live" / date / "toda" / f"{race_no:02d}"
            documents = {
                name: _load(live_root / f"{name}.json")
                for name in ("direct", "exhibition", "original_exhibition")
            }
            if not apply_live_review(prediction, documents):
                raise AssertionError(f"live review failed for {date} {race_no}R")
            prediction["flyPrediction"] = build_fly_prediction(
                prediction,
                (prediction.get("modelInputs") or {}).get("racers") or [],
            )
            predictions[(date, race_no)] = prediction

    # Results are deliberately loaded only after every prediction is complete.
    rows = {}
    for key, prediction in predictions.items():
        date, race_no = key
        result = _load(REPO_ROOT / "data" / "live" / date / "toda" / f"{race_no:02d}" / "result.json")
        rows[key] = {
            "prediction": prediction,
            "result": "-".join(str(value) for value in result["data"]["order"][:3]),
        }
    return rows


class TodaFull31RaceReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _replay()

    def test_all_31_races_have_fixed_unique_scenario_grounded_tickets(self):
        self.assertEqual(len(self.rows), 31)
        for prediction_row in self.rows.values():
            prediction = prediction_row["prediction"]
            tickets = prediction["ai"]
            roles = [ticket["role"] for ticket in tickets]
            self.assertEqual(len(tickets), 10)
            self.assertEqual(len({ticket["combo"] for ticket in tickets}), 10)
            self.assertEqual(roles.count("本線"), 6)
            self.assertEqual(roles.count("2着ズレ"), 1)
            self.assertEqual(roles.count("3着ズレ"), 1)
            self.assertEqual(roles.count("シナリオ穴"), 2)
            scenario_ids_by_head = {}
            for scenario in prediction["scenarios"]:
                scenario_ids_by_head.setdefault(int(scenario["head"]), set()).add(scenario["id"])
            for ticket in tickets:
                if ticket["role"] != "シナリオ穴":
                    continue
                head = int(ticket["combo"].split("-")[0])
                self.assertIn(head, scenario_ids_by_head)
                self.assertTrue(set(ticket["scenarioIds"]) <= scenario_ids_by_head[head])
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(sum(prediction[key].values()), 100.0)
            self.assertFalse(prediction["sourceSummary"]["odds_used_for_probability"])
            self.assertFalse(prediction["sourceSummary"]["odds_used_for_tickets"])
            self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForProbability"])
            self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForTickets"])
            normal_combos = {ticket["combo"] for ticket in tickets}
            fly_tickets = prediction["flyPrediction"]["tickets"]
            fly_combos = {ticket["combo"] for ticket in fly_tickets}
            self.assertEqual(len(fly_tickets), 10)
            self.assertEqual(len(fly_combos), 10)
            self.assertFalse(normal_combos & fly_combos)
            self.assertTrue(all(not combo.startswith("1-") for combo in fly_combos))

    def test_required_regression_races(self):
        race6 = self.rows[("2026-09-28", 6)]["prediction"]
        self.assertEqual(max(race6["win"], key=race6["win"].get), "4")
        self.assertEqual(next(row["role"] for row in race6["ai"] if row["combo"] == "4-2-5"), "3着ズレ")
        for race_no in (2, 3):
            scenarios = self.rows[("2026-09-28", race_no)]["prediction"]["scenarios"]
            self.assertTrue(any(row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE" for row in scenarios))
        race4 = self.rows[("2026-09-28", 4)]["prediction"]["scenarios"]
        self.assertFalse(any(row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE" for row in race4))
        for race_no in range(1, 13):
            scenarios = self.rows[("2026-09-26", race_no)]["prediction"]["scenarios"]
            self.assertFalse(any(row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE" for row in scenarios))


if __name__ == "__main__":
    unittest.main()
