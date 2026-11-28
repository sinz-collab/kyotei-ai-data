"""Missing BOATERS fields must not stop all Toda morning predictions."""

import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


RUNNER = Path(__file__).resolve().parents[1] / "automation" / "apply_toda_v5.py"


class TodaOptionalSupplementsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine = types.ModuleType("toda_prediction_engine_v5")
        engine.ENGINE_ID = "toda_test_engine"

        class Engine:
            def predict(self, race, _context):
                return {
                    "win": {str(i): 100.0 / 6 for i in range(1, 7)},
                    "second": {str(i): 100.0 / 6 for i in range(1, 7)},
                    "third": {str(i): 100.0 / 6 for i in range(1, 7)},
                    "sab": {}, "ai": [], "aiUpset": [], "sourceSummary": {},
                    "modelInputs": {"racers": race["racers"]},
                }

        engine.TodaPredictionEngineV5 = Engine
        fly = types.ModuleType("toda_fly_prediction_v5")

        def build_fly(_prediction, racers, status):
            self_value = next(row for row in racers if row["lane"] == 3).get("boaters_makuri_rate")
            if self_value is not None and not math.isfinite(float(self_value)):
                raise RuntimeError("toda_fly_attack_rate_missing: boaters_makuri_rate")
            return {"status": status}

        fly.build_fly_prediction = build_fly
        spec = importlib.util.spec_from_file_location("toda_optional_test_runner", RUNNER)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"toda_prediction_engine_v5": engine, "toda_fly_prediction_v5": fly}):
            spec.loader.exec_module(module)
        cls.runner = module

    def test_invalid_optional_rate_is_removed_without_changing_input(self):
        original = [{"lane": 3, "boaters_makuri_rate": "—", "boaters_sashi_rate": "12.5"}]
        sanitized = self.runner.fly_racers_without_missing_supplements(original)
        self.assertNotIn("boaters_makuri_rate", sanitized[0])
        self.assertEqual(sanitized[0]["boaters_sashi_rate"], "12.5")
        self.assertEqual(original[0]["boaters_makuri_rate"], "—")

    def test_one_missing_rate_does_not_cancel_twelve_races(self):
        races = [
            {"race": n, "racers": [{"lane": lane, "boaters_makuri_rate": (
                "未取得" if n == 9 and lane == 3 else "5.0"
            )} for lane in range(1, 7)]}
            for n in range(1, 13)
        ]
        payload = {"venueId": "toda", "venue": "戸田", "date": "2026-11-28", "races": races}
        result = self.runner.apply_toda_v5(payload, "2026-11-28")
        self.assertEqual(len(result["preds"]), 12)
        self.assertEqual(result["preds"]["9"]["flyPrediction"]["status"], "pre")
        self.assertEqual(races[8]["racers"][2]["boaters_makuri_rate"], "未取得")


if __name__ == "__main__":
    unittest.main()
