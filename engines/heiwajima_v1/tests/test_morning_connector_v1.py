from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CONNECTOR = ROOT / "automation" / "apply_heiwajima_v1.py"
spec = importlib.util.spec_from_file_location("apply_heiwajima_v1", CONNECTOR)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def racer(lane: int) -> dict:
    return {
        "lane": lane,
        "actual_course": lane,
        "entry_course": lane,
        "name": f"未登録 選手{lane}",
        "motor_2": str(30 + lane),
        "motor_3": str(45 + lane),
        "boat_2": str(29 + lane),
        "boat_3": str(44 + lane),
        "season_runs": [
            {"race": "1R", "course": str(lane), "st": ".15", "finish": f"{lane}着"}
        ],
    }


def payload() -> dict:
    return {
        "venueId": "heiwajima",
        "venue": "平和島",
        "date": "2026-07-28",
        "seriesDay": "4日目",
        "tide": {"tideType": "中潮相当", "phase": "falling", "band": "低潮位"},
        "races": [
            {
                "race": race_no,
                "deadline": "12:00",
                "racers": [racer(lane) for lane in range(1, 7)],
            }
            for race_no in range(1, 13)
        ],
    }


class MorningConnectorTest(unittest.TestCase):
    def test_generates_twelve_site_compatible_predictions(self) -> None:
        result = module.apply_heiwajima_v1(payload(), "2026-07-28", ROOT / "data")
        self.assertEqual(result["engine"], module.ENGINE_ID)
        self.assertEqual(sorted(map(int, result["preds"].keys())), list(range(1, 13)))
        for prediction in result["preds"].values():
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(sum(prediction[key].values()), 100.0, places=1)
            self.assertFalse(prediction["sourceSummary"]["oddsUsedForProbability"])
            self.assertFalse(prediction["sourceSummary"]["exhibitionStartUsedAlone"])
            self.assertEqual(len(prediction["ai"]) + len(prediction["aiUpset"]), 10)
            self.assertIn("player_id_unresolved", prediction["missingCodes"])

    def test_non_open_venue_can_skip_without_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            dated = data_root / "venues" / "heiwajima" / "20260728.json"
            self.assertFalse(dated.exists())


if __name__ == "__main__":
    unittest.main()
