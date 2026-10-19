from __future__ import annotations

import sys
import unittest
from pathlib import Path


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from run_ashiya_v16 import build_site_prediction
from run_ashiya_v16_live import attach_lane1_fly_comparison


class AshiyaFlyProbabilityStageTests(unittest.TestCase):
    def test_morning_probability_is_lane1_win_complement(self) -> None:
        result = {
            "probabilities": [
                {"lane": 1, "win": 0.6125, "second": 0.10, "third": 0.12},
                {"lane": 2, "win": 0.10, "second": 0.20, "third": 0.18},
                {"lane": 3, "win": 0.08, "second": 0.19, "third": 0.18},
                {"lane": 4, "win": 0.07, "second": 0.18, "third": 0.18},
                {"lane": 5, "win": 0.0675, "second": 0.17, "third": 0.17},
                {"lane": 6, "win": 0.07, "second": 0.16, "third": 0.17},
            ],
            "tickets": {
                "main": ["1-2-3", "1-2-4", "1-2-5", "1-2-6", "1-3-2", "1-3-4"],
                "deviation": ["2-1-3", "2-1-4", "2-1-5"],
                "upset": ["3-1-2"],
            },
            "sab": {"grade": "A"},
        }

        legacy, native = build_site_prediction(result, 1, "pre")

        for prediction in (legacy, native):
            self.assertEqual(prediction["lane1FlyProbability"], 38.75)
            self.assertEqual(prediction["lane1FlyProbabilityMorning"], 38.75)
            self.assertEqual(prediction["lane1FlyProbabilitySource"], "morning_simple")
            self.assertEqual(prediction["lane1FlyProbabilityDisplayStage"], "morning")
            self.assertTrue(prediction["lane1FlyProbabilityIsProvisional"])

    def test_live_comparison_uses_saved_morning_value(self) -> None:
        old = {
            "win": {"1": 61.25},
            "lane1FlyProbabilityMorning": 38.75,
        }
        legacy = {
            "lane1FlyProbability": 46.2,
            "lane1FlyProbabilityLive": 46.2,
            "lane1FlyProbabilitySource": "live",
        }
        native = dict(legacy)

        attach_lane1_fly_comparison(old, legacy, native)

        for prediction in (legacy, native):
            self.assertEqual(prediction["lane1FlyProbabilityMorning"], 38.75)
            self.assertEqual(prediction["lane1FlyProbabilityLive"], 46.2)
            self.assertEqual(prediction["lane1FlyProbabilityDeltaPt"], 7.45)
            self.assertEqual(prediction["lane1FlyProbability"], 46.2)

    def test_legacy_morning_json_falls_back_to_lane1_win_complement(self) -> None:
        old = {"win": {"1": 57.35}}
        legacy = {
            "lane1FlyProbability": 31.1,
            "lane1FlyProbabilitySource": "live",
        }
        native = dict(legacy)

        attach_lane1_fly_comparison(old, legacy, native)

        self.assertEqual(legacy["lane1FlyProbabilityMorning"], 42.65)
        self.assertEqual(legacy["lane1FlyProbabilityLive"], 31.1)
        self.assertEqual(legacy["lane1FlyProbabilityDeltaPt"], -11.55)


if __name__ == "__main__":
    unittest.main()
