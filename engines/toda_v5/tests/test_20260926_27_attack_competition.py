from copy import deepcopy
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


def _replay(compact_date):
    payload = _load(REPO_ROOT / "data" / "venues" / "toda" / f"{compact_date}.json")
    live_day = f"{compact_date[:4]}-{compact_date[4:6]}-{compact_date[6:8]}"
    rows = {}
    for race in payload["races"]:
        race_no = int(race["race"])
        prediction = TodaPredictionEngineV5().predict(race, _context(payload, race))
        live_root = REPO_ROOT / "data" / "live" / live_day / "toda" / f"{race_no:02d}"
        documents = {
            name: _load(live_root / f"{name}.json")
            for name in ("direct", "exhibition", "original_exhibition")
            if (live_root / f"{name}.json").is_file()
        }
        assert apply_live_review(prediction, documents)
        result = _load(live_root / "result.json")
        winner = int(result["data"]["order"][0])
        top = int(max(prediction["win"], key=prediction["win"].get))
        rows[race_no] = {"prediction": prediction, "winner": winner, "top": top}
    return rows


class TodaAttackCompetitionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.day26 = _replay("20260926")
        cls.day27 = _replay("20260927")

    def test_20260926_keeps_six_hits_and_every_existing_correct_top(self):
        hits = {race for race, row in self.day26.items() if row["top"] == row["winner"]}
        self.assertEqual(hits, {5, 7, 9, 10, 11, 12})
        for row in self.day26.values():
            scenarios = row["prediction"]["scenarios"]
            self.assertFalse(any(s.get("currentFormCompetitionApplied") for s in scenarios))

    def test_20260927_keeps_six_hits_and_required_tops(self):
        hits = {race for race, row in self.day27.items() if row["top"] == row["winner"]}
        self.assertEqual(hits, {1, 2, 4, 5, 6, 12})
        self.assertEqual(
            {race: self.day27[race]["top"] for race in (2, 4, 5, 6, 12)},
            {2: 3, 4: 2, 5: 4, 6: 2, 12: 1},
        )

    def test_fixed_competition_strengths_and_current_form_gates(self):
        race4 = {s["head"]: s for s in self.day27[4]["prediction"]["scenarios"]}
        self.assertEqual(race4[2]["attackEstablishmentMultiplier"], 1.18)
        self.assertEqual(race4[4]["attackEstablishmentMultiplier"], 1.0)
        self.assertEqual(race4[4]["currentForm"]["finishes"], [1, 1])
        self.assertFalse(race4[4]["currentFormCompetitionApplied"])

        race6 = {s["head"]: s for s in self.day27[6]["prediction"]["scenarios"]}
        self.assertEqual(race6[2]["attackEstablishmentMultiplier"], 1.18)
        self.assertEqual(race6[3]["attackEstablishmentMultiplier"], .72)
        self.assertEqual(race6[3]["currentForm"]["finishes"], [5, 5])
        self.assertTrue(race6[3]["currentFormCompetitionApplied"])

    def test_all_public_probabilities_are_normalized_and_odds_are_unused(self):
        for rows in (self.day26, self.day27):
            for row in rows.values():
                prediction = row["prediction"]
                for key in ("win", "second", "third"):
                    self.assertAlmostEqual(sum(prediction[key].values()), 100.0)
                self.assertFalse(prediction["sourceSummary"]["odds_used_for_probability"])
                self.assertFalse(prediction["sourceSummary"]["odds_used_for_tickets"])
                self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForProbability"])
                self.assertFalse(prediction["liveReviewMeta"]["oddsUsedForTickets"])
                self.assertTrue(all(ticket["odds"] == "-" for ticket in prediction["ai"]))

    def test_exhibition_start_alone_is_only_a_small_modifier(self):
        payload = _load(REPO_ROOT / "data" / "venues" / "toda" / "20260927.json")
        race = payload["races"][5]
        live_root = REPO_ROOT / "data" / "live" / "2026-09-27" / "toda" / "06"
        direct = _load(live_root / "direct.json")
        exhibition = _load(live_root / "exhibition.json")

        def reviewed(start_time):
            prediction = TodaPredictionEngineV5().predict(race, _context(payload, race))
            changed = deepcopy(exhibition)
            for entry in changed["data"]["entries"]:
                if int(entry["lane"]) == 2:
                    entry["start_time"] = start_time
            self.assertTrue(apply_live_review(prediction, {"direct": direct, "exhibition": changed}))
            return prediction

        fast = reviewed("-.01")
        slow = reviewed(".25")
        self.assertLessEqual(abs(fast["win"]["2"] - slow["win"]["2"]), 3.0)
        self.assertFalse(fast["sourceSummary"]["exhibition_st_used_alone"])


if __name__ == "__main__":
    unittest.main()
