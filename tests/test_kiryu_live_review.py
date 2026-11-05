from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from run_kiryu_v1 import complete_live_document, decorate_live_prediction


class KiryuLiveReviewTests(unittest.TestCase):
    def test_live_prediction_records_complete_review_and_zero_deltas(self) -> None:
        morning = {
            position: {str(lane): 100 / 6 for lane in range(1, 7)}
            for position in ("win", "second", "third")
        }
        live = {
            **{position: values.copy() for position, values in morning.items()},
            "predictionStage": "live",
        }
        documents = {
            name: {"fetched_at": f"2026-11-05T16:0{index}:00+09:00"}
            for index, name in enumerate(("direct", "exhibition", "original_exhibition"))
        }

        decorate_live_prediction(live, morning, documents)

        self.assertEqual(live["status"], "complete")
        self.assertEqual(live["finalPredictionStatus"], "complete")
        self.assertEqual(live["probabilityReviewStatus"], "reviewed")
        self.assertTrue(live["probabilityFlow"]["realtimeApplied"])
        self.assertTrue(live["probabilityFlow"]["reviewed"])
        self.assertTrue(live["liveInput"]["complete"])
        self.assertEqual(live["probabilityReview"]["1"]["deltaWin"], 0.0)

    def test_live_document_requires_all_six_lanes(self) -> None:
        complete = {
            "status": "complete",
            "complete": True,
            "data": {"entries": [{"lane": lane} for lane in range(1, 7)]},
        }
        self.assertTrue(complete_live_document(complete))
        complete["data"]["entries"].pop()
        self.assertFalse(complete_live_document(complete))

        direct = {
            "status": "complete",
            "complete": True,
            "data": {"racers": [{"lane": lane} for lane in range(1, 7)]},
        }
        self.assertTrue(complete_live_document(direct, "direct"))


if __name__ == "__main__":
    unittest.main()
