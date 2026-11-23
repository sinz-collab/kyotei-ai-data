import copy
import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from run_official_predictions import (
    COMMANDS, merge_missing, merge_supplement, missing_races, supplement_hash, supplement_races,
)


class OfficialPredictionStageTests(unittest.TestCase):
    def test_naruto_and_ashiya_reuse_frozen_venue_engines(self):
        self.assertEqual(COMMANDS["naruto"], ("run_naruto_v5.py", "--stage", "morning"))
        self.assertEqual(COMMANDS["ashiya"], ("run_ashiya_v16.py", "--stage", "pre"))
        self.assertEqual(COMMANDS["shimonoseki"], ("run_shimonoseki_v6_1.py", "--stage", "preliminary"))
        recovery = (Path(__file__).resolve().parents[1] / ".github/workflows/recover-official-predictions.yml").read_text()
        self.assertIn("data/venues/naruto data/venues/ashiya", recovery)
        self.assertIn("data/venues/omura data/venues/shimonoseki", recovery)
        self.assertIn("lightgbm==4.6.0 scikit-learn==1.8.0", recovery)
        self.assertIn("Official prediction engines failed", recovery)
        morning = (Path(__file__).resolve().parents[1] / ".github/workflows/morning-data-official.yml").read_text()
        self.assertIn("lightgbm==4.6.0 scikit-learn==1.8.0", morning)

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

    def test_supplement_stage_only_recalculates_changed_preliminary_inputs(self):
        morning = datetime(2026, 11, 19, 12, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.payload["sourceStatus"] = {"official_morning": {"status": "published_supplement"}}
        for i in (2, 3, 4):
            self.payload["preds"][str(i)] = {
                "win": {"1": 50}, "officialSupplementHash": supplement_hash(self.payload["races"][i-1]),
                "prediction_history": {"morning": [{"original": True}]},
            }
        for i in (2, 3, 4):
            self.payload["races"][i-1]["racers"] = [{"boaters_local_avg_st": ".13"}]
        self.payload["preds"]["3"]["prediction_history"]["live"] = [{"final": True}]
        self.payload["preds"]["4"]["officialPredictionStage"] = "supplemented"
        self.assertEqual(supplement_races(self.payload, morning), {2})

        generated = copy.deepcopy(self.payload)
        generated["preds"]["2"] = {"win": {"1": 60}, "tickets": ["1-2-3"]}
        generated["races"][1]["live"] = {"unexpected": "overwrite"}
        result, count = merge_supplement(self.payload, generated, {2})
        self.assertEqual(count, 1)
        self.assertEqual(result["preds"]["2"]["win"]["1"], 60)
        self.assertEqual(result["preds"]["2"]["officialPreliminarySummary"]["win"]["1"], 50)
        self.assertEqual(result["preds"]["2"]["prediction_history"], self.payload["preds"]["2"]["prediction_history"])
        self.assertEqual(result["races"][1]["live"], self.payload["races"][1]["live"])
        self.assertEqual(result["preds"]["3"], self.payload["preds"]["3"])

    def test_supplement_reexecution_and_deadlines_are_safe(self):
        now = datetime(2026, 11, 19, 12, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.payload["sourceStatus"] = {"official_morning": {"status": "published_supplement"}}
        row = self.payload["races"][9]
        self.payload["preds"]["10"] = {
            "officialSupplementHash": supplement_hash(row),
            "officialPredictionStage": "preliminary",
        }
        self.assertEqual(supplement_races(self.payload, now), set())
        row["racers"] = [{"motor_recent": {"runs_count": 8}}]
        self.assertEqual(supplement_races(self.payload, now), {10})
        self.payload["preds"]["10"]["officialPredictionStage"] = "supplemented"
        self.assertEqual(supplement_races(self.payload, now), set())
        self.payload["preds"]["10"]["officialPredictionStage"] = "preliminary"
        row["deadline"] = "11:59"
        self.assertEqual(supplement_races(self.payload, now), set())


if __name__ == "__main__":
    unittest.main()
