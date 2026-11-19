import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from run_official_predictions import merge_missing, missing_races


class OfficialPredictionStageTests(unittest.TestCase):
    def setUp(self):
        self.payload = {
            "date": "2026-11-19", "venueId": "toda",
            "preds": {"1": {"prediction_history": {"live": [{"marker": "keep"}]}}},
            "races": [
                {"race": i, "deadline": "20:00", "racers": [], "live": {"marker": "live"}}
                for i in range(1, 13)
            ],
        }

    def test_only_unpredicted_and_not_closed_races_are_eligible(self):
        self.payload["races"][1]["deadline"] = "10:00"
        now = datetime(2026, 11, 19, 12, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertEqual(missing_races(self.payload, now), set(range(3, 13)))

    def test_merge_keeps_existing_live_history_and_core_data(self):
        original = copy.deepcopy(self.payload)
        generated = copy.deepcopy(self.payload)
        generated["preds"] = {str(i): {"tickets": ["1-2-3"]} for i in range(1, 13)}
        generated["races"][1]["prediction"] = {"marker": "new"}
        generated["races"][1]["live"] = {"marker": "overwritten"}
        result, count = merge_missing(self.payload, generated, {2})
        self.assertEqual(count, 1)
        self.assertEqual(result["preds"]["1"], original["preds"]["1"])
        self.assertEqual(result["races"][1]["live"], original["races"][1]["live"])
        self.assertEqual(result["races"][1]["prediction"], {"marker": "new"})
        self.assertEqual(result["predictionStatus"], "partial")


if __name__ == "__main__":
    unittest.main()
