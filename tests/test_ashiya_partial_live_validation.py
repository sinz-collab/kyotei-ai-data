from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))

from run_ashiya_v16 import ENGINE_NAME, ENGINE_VERSION, validate_final_payload


def sample_payload(keys: set[str]) -> dict:
    probability = {str(lane): (16.67 if lane < 6 else 16.65) for lane in range(1, 7)}
    prediction = {
        "win": probability,
        "second": probability,
        "third": probability,
        "sab": "B",
        "tickets": [f"{lane}-1-2" for lane in range(1, 11)],
    }
    return {
        "date": "2026-11-20",
        "venueId": "ashiya",
        "engine": ENGINE_NAME,
        "engineVersion": ENGINE_VERSION,
        "races": [
            {"race": race, "racers": [{"lane": lane} for lane in range(1, 7)]}
            for race in range(1, 13)
        ],
        "preds": {key: dict(prediction) for key in keys},
    }


class AshiyaPartialLiveValidationTests(unittest.TestCase):
    def test_live_accepts_existing_partial_predictions(self) -> None:
        payload = sample_payload({str(race) for race in range(3, 13)})
        validate_final_payload(payload, "2026-11-20", allow_partial=True)

    def test_morning_still_requires_all_twelve_predictions(self) -> None:
        payload = sample_payload({str(race) for race in range(3, 13)})
        with self.assertRaisesRegex(RuntimeError, "ashiya_final_predictions_invalid"):
            validate_final_payload(payload, "2026-11-20")

    def test_live_rejects_invalid_existing_prediction(self) -> None:
        payload = sample_payload({"7"})
        payload["preds"]["7"]["tickets"] = []
        with self.assertRaisesRegex(RuntimeError, "ashiya_07_tickets_invalid"):
            validate_final_payload(payload, "2026-11-20", allow_partial=True)

    def test_live_rejects_unknown_race_and_empty_predictions(self) -> None:
        for keys in ({"13"}, set()):
            with self.subTest(keys=keys):
                with self.assertRaisesRegex(RuntimeError, "ashiya_final_predictions_invalid"):
                    validate_final_payload(sample_payload(keys), "2026-11-20", allow_partial=True)


if __name__ == "__main__":
    unittest.main()
