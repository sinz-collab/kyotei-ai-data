import json
from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_live_review_v5 import apply_live_review
from toda_prediction_engine_v5 import TodaPredictionEngineV5


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
    payload = _load(REPO_ROOT / "data" / "venues" / "toda" / "20260928.json")
    rows = {}
    for race in payload["races"][:7]:
        race_no = int(race["race"])
        prediction = TodaPredictionEngineV5().predict(race, _context(payload, race))
        live_root = REPO_ROOT / "data" / "live" / "2026-09-28" / "toda" / f"{race_no:02d}"
        documents = {
            name: _load(live_root / f"{name}.json")
            for name in ("direct", "exhibition", "original_exhibition")
        }
        if not apply_live_review(prediction, documents):
            raise AssertionError(f"live review failed for race {race_no}")
        result = _load(live_root / "result.json")
        rows[race_no] = {
            "prediction": prediction,
            "result": "-".join(str(value) for value in result["data"]["order"][:3]),
        }
    return rows


class Toda20260928CurrentFormTicketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = _replay()

    def test_good_inside_resistance_and_poor_inside_non_activation(self):
        for race_no in (2, 3):
            scenarios = self.rows[race_no]["prediction"]["scenarios"]
            self.assertTrue(any(row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE" for row in scenarios))
        race4 = self.rows[4]["prediction"]["scenarios"]
        self.assertFalse(any(row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE" for row in race4))

    def test_race6_keeps_425_as_third_drift(self):
        tickets = self.rows[6]["prediction"]["ai"]
        ticket = next(row for row in tickets if row["combo"] == "4-2-5")
        self.assertEqual(ticket["role"], "3着ズレ")

    def test_all_seven_races_have_fixed_unique_tickets_and_no_ai_upset_overlap(self):
        for row in self.rows.values():
            prediction = row["prediction"]
            combos = {ticket["combo"] for ticket in prediction["ai"]}
            upset = {ticket["combo"] for ticket in prediction["aiUpset"]}
            self.assertEqual(len(prediction["ai"]), 10)
            self.assertEqual(len(combos), 10)
            self.assertFalse(combos & upset)
            self.assertEqual([ticket["role"] for ticket in prediction["ai"]].count("本線"), 6)
            self.assertEqual([ticket["role"] for ticket in prediction["ai"]].count("2着ズレ"), 1)
            self.assertEqual([ticket["role"] for ticket in prediction["ai"]].count("3着ズレ"), 1)
            roles = [ticket["role"] for ticket in prediction["ai"]]
            self.assertEqual(roles.count("シナリオ穴") + roles.count("展開保険"), 2)
            scenario_heads = {int(scenario["head"]) for scenario in prediction["scenarios"]}
            holes = [ticket for ticket in prediction["ai"] if ticket["role"] == "シナリオ穴"]
            self.assertTrue(all(int(ticket["combo"].split("-")[0]) in scenario_heads for ticket in holes))
            self.assertTrue(all(ticket["scenarioIds"] for ticket in holes))
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(sum(prediction[key].values()), 100.0)
            self.assertFalse(prediction["sourceSummary"]["odds_used_for_probability"])
            self.assertFalse(prediction["sourceSummary"]["odds_used_for_tickets"])
            self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForProbability"])
            self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForTickets"])


if __name__ == "__main__":
    unittest.main()
