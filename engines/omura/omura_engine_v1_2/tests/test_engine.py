from __future__ import annotations
import json, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from omura_engine import OmuraPredictionEngine

class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = OmuraPredictionEngine("/mnt/data", ROOT/"config"/"engine_config.json")
        cls.payload = json.loads((ROOT/"samples"/"sample_input.json").read_text(encoding="utf-8"))
        cls.result = cls.engine.predict(cls.payload)

    def test_probability_sums(self):
        checks = self.result["probabilities"]["sum_check"]
        self.assertAlmostEqual(checks["win"], 1.0, places=6)
        self.assertAlmostEqual(checks["second"], 1.0, places=6)
        self.assertAlmostEqual(checks["third"], 1.0, places=6)

    def test_ten_unique_tickets(self):
        tickets = self.result["tickets"]
        self.assertEqual(len(tickets["main"]), 6)
        self.assertEqual(len(tickets["deviation"]), 2)
        self.assertEqual(len(tickets["upset"]), 2)
        all_tickets = [x["ticket"] for x in tickets["all"]]
        self.assertEqual(len(all_tickets), 10)
        self.assertEqual(len(set(all_tickets)), 10)

    def test_lane1_not_venue_average_locked(self):
        lane1 = next(x for x in self.result["probabilities"]["boats"] if x["lane"] == 1)
        self.assertLessEqual(lane1["win"], 0.42 + 1e-9)

    def test_sab_independent(self):
        self.assertTrue(self.result["sab"]["independent_of_ticket_count"])
        self.assertTrue(self.result["sab"]["independent_of_odds"])
        self.assertFalse(self.result["odds_used_for_prediction"])

    def test_entry_change_recalculates(self):
        changed = json.loads(json.dumps(self.payload))
        changed["boats"][1]["entry_course"] = 3
        changed["boats"][2]["entry_course"] = 2
        result2 = self.engine.predict(changed)
        original = [x["win"] for x in self.result["probabilities"]["boats"]]
        altered = [x["win"] for x in result2["probabilities"]["boats"]]
        self.assertNotEqual(original, altered)

if __name__ == "__main__":
    unittest.main()
