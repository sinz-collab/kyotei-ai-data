from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from build_site_data import attach_independent_race_domains


def probability_map() -> dict[str, float]:
    return {str(lane): 100.0 / 6.0 for lane in range(1, 7)}


def native_prediction() -> dict:
    return {
        "status": "ready",
        "engine": "ashiya_prediction_engine",
        "engine_version": "1.6.1",
        "stage": "live",
        "probabilities": {
            "win": probability_map(),
            "second": probability_map(),
            "third": probability_map(),
        },
        "sab": {"grade": "A", "score": 0.8},
        "tickets": {
            "main": [f"main-{index}" for index in range(6)],
            "deviation": [f"deviation-{index}" for index in range(2)],
            "upset": [f"upset-{index}" for index in range(2)],
            "all": [f"ticket-{index}" for index in range(10)],
        },
        "attack_structure": {"head_candidates": [{"lane": 1}]},
        "scenarios": [{"name": "inside"}],
        "audit": {"model": {"missing_features": ["feature-a"]}},
    }


def payload(slug: str = "ashiya") -> dict:
    prediction = native_prediction()
    return {
        "venueId": slug,
        "date": "2026-08-20",
        "engine": "ashiya_prediction_engine",
        "engineVersion": "1.6.1",
        "preds": {"1": {"win": probability_map()}},
        "races": [
            {
                "race": 1,
                "deadline": "08:32",
                "racers": [{"lane": lane, "name": f"Racer {lane}"} for lane in range(1, 7)],
                "prediction": prediction,
            }
        ],
    }


class AshiyaNativePredictionPreservationTests(unittest.TestCase):
    def test_complete_ashiya_native_prediction_is_preserved(self) -> None:
        value = payload()
        expected = deepcopy(value["races"][0]["prediction"])

        result = attach_independent_race_domains(value, "ashiya", True, "")

        self.assertEqual(result["races"][0]["prediction"], expected)

    def test_incomplete_ashiya_native_prediction_uses_envelope(self) -> None:
        value = payload()
        value["races"][0]["prediction"]["tickets"]["all"].pop()

        result = attach_independent_race_domains(value, "ashiya", True, "")

        self.assertEqual(result["races"][0]["prediction"]["status"], "ready")
        self.assertNotIn("attack_structure", result["races"][0]["prediction"])

    def test_other_venue_logic_is_unchanged(self) -> None:
        value = payload("toda")

        result = attach_independent_race_domains(value, "toda", True, "")

        self.assertNotIn("attack_structure", result["races"][0]["prediction"])


if __name__ == "__main__":
    unittest.main()
