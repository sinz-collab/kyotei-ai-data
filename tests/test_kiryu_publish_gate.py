from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from build_site_data import kiryu_v1_prediction_is_complete, prediction_payload_gate


def prediction() -> dict:
    probabilities = {str(lane): 100 / 6 for lane in range(1, 7)}
    normal = [
        "1-2-3", "1-2-4", "1-3-2", "1-3-4", "2-1-3",
        "2-1-4", "3-1-2", "3-1-4", "4-1-2", "4-1-3",
    ]
    fly = [
        "5-1-2", "5-1-3", "5-1-4", "5-1-6",
        "6-1-2", "6-1-3", "6-1-4", "6-1-5",
        "2-1-3", "2-1-4",
    ]
    return {
        "modelVersion": "kiryu_v1_20261104",
        "predictionStage": "morning",
        "win": probabilities,
        "second": probabilities,
        "third": probabilities,
        "tickets": [{"combo": combo} for combo in normal],
        "flyPrediction": {
            "status": "morning",
            "modelVersion": "kiryu_independent_variable_20261106",
            "allocation": [4, 4, 2],
            "headCandidates": [5, 6, 2],
            "tickets": [{"combo": combo} for combo in fly],
        },
    }


class KiryuPublishGateTests(unittest.TestCase):
    def test_accepts_complete_independent_variable_allocation(self) -> None:
        value = prediction()
        self.assertTrue(kiryu_v1_prediction_is_complete(value))
        payload = {
            "date": "2026-11-05",
            "engine": "kiryu_v1",
            "preds": {str(race): copy.deepcopy(value) for race in range(1, 13)},
        }
        self.assertEqual(prediction_payload_gate(payload, "2026-11-05"), (True, "ok"))

    def test_rejects_missing_fly_ticket_and_legacy_combination_key(self) -> None:
        missing = prediction()
        missing["flyPrediction"]["tickets"].pop()
        self.assertFalse(kiryu_v1_prediction_is_complete(missing))

        legacy_key = prediction()
        ticket = legacy_key["flyPrediction"]["tickets"][0]
        ticket["combination"] = ticket.pop("combo")
        self.assertFalse(kiryu_v1_prediction_is_complete(legacy_key))

    def test_accepts_concentrated_six_plus_four(self) -> None:
        value = prediction()
        value["flyPrediction"]["allocation"] = [6, 4]
        value["flyPrediction"]["headCandidates"] = [5, 6]
        value["flyPrediction"]["tickets"] = [
            {"combo": combo} for combo in (
                "5-1-2", "5-1-3", "5-1-4", "5-1-6", "5-2-1", "5-2-3",
                "6-1-2", "6-1-3", "6-1-4", "6-1-5",
            )
        ]
        self.assertTrue(kiryu_v1_prediction_is_complete(value))

    def test_rejects_broken_variable_allocation(self) -> None:
        value = prediction()
        value["flyPrediction"]["tickets"][3]["combo"] = "6-2-3"
        self.assertFalse(kiryu_v1_prediction_is_complete(value))

    def test_live_runner_can_preserve_same_day_frozen_fly_tickets(self) -> None:
        value = prediction()
        value["predictionStage"] = "live"
        value["flyPrediction"] = {
            "status": "live",
            "modelVersion": "kiryu_independent_6plus4_frozen_20261104",
            "allocation": [6, 4],
            "tickets": [
                {"combo": combo} for combo in (
                    "1-2-3", "1-2-4", "1-2-5", "1-3-2", "1-3-4", "1-4-2",
                    "5-1-2", "5-1-3", "5-1-4", "5-2-1",
                )
            ],
        }
        self.assertFalse(kiryu_v1_prediction_is_complete(value))
        self.assertTrue(
            kiryu_v1_prediction_is_complete(
                value,
                allow_legacy_frozen_fly=True,
            )
        )


if __name__ == "__main__":
    unittest.main()
