from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "scripts")]

from run_kiryu_v1 import (
    attach_live_display,
    build_live_calculation_input,
    complete_live_document,
    decorate_live_prediction,
    require_live_probability_calculation,
)
from audit_kiryu_live import FAILURE_REASON, fallback_prediction


class KiryuLiveReviewTests(unittest.TestCase):
    def test_live_documents_are_attached_to_the_public_display_domain(self) -> None:
        documents = {
            "direct": {
                "fetched_at": "2026-11-07T16:04:39+09:00",
                "data": {"weather": "曇り", "wind_speed": 2},
            },
            "exhibition": {
                "fetched_at": "2026-11-07T16:04:40+09:00",
                "data": {"entries": [{"lane": lane} for lane in range(1, 7)]},
            },
            "original_exhibition": {
                "fetched_at": "2026-11-07T16:04:41+09:00",
                "data": {"entries": [
                    {
                        "lane": lane,
                        "half_lap_time": 19 + lane / 100,
                        "turn_time": 5 + lane / 100,
                        "straight_time": 7 + lane / 100,
                    }
                    for lane in range(1, 7)
                ]},
            },
        }
        prediction = {"predictionStage": "live", "win": {"1": 30.0}}
        race = {"race": 3, "prediction": prediction, "live": {"retained": True}}

        attach_live_display(race, documents)

        self.assertIs(race["prediction"], prediction)
        self.assertEqual(race["prediction"], {"predictionStage": "live", "win": {"1": 30.0}})
        self.assertTrue(race["live"]["retained"])
        self.assertTrue(race["live"]["originalExhibitionAvailable"])
        self.assertEqual(set(race["live"]["original"]), set("123456"))
        self.assertEqual(
            set(race["live"]["original"]["1"]),
            {"lane", "half_lap_time", "turn_time", "straight_time"},
        )
        self.assertEqual(
            race["live"]["sourceFetchedAt"]["original_exhibition"],
            "2026-11-07T16:04:41+09:00",
        )

    def test_live_prediction_rejects_unexecuted_calculation(self) -> None:
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

        with self.assertRaisesRegex(RuntimeError, "without calculation"):
            decorate_live_prediction(
                live,
                morning,
                documents,
                {"executed": False, "status": "not_executed", "inputHash": "abc"},
            )
        with self.assertRaisesRegex(RuntimeError, "omit required probability features"):
            decorate_live_prediction(
                live,
                morning,
                documents,
                {"executed": True, "status": "success", "inputHash": "abc",
                 "probabilityFeaturesUsed": ["exhibition_course"]},
            )

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

    def test_live_input_hash_covers_all_probability_features(self) -> None:
        documents = {
            "direct": {
                "status": "complete",
                "complete": True,
                "data": {
                    "racers": [{"lane": lane} for lane in range(1, 7)],
                    "actual_entry": list(range(1, 7)),
                    "weather": "曇り",
                    "air_temperature": 13,
                    "water_temperature": 16,
                    "wind_direction": 4,
                    "wind_speed": 2,
                    "wave_height": 1,
                },
            },
            "exhibition": {
                "status": "complete",
                "complete": True,
                "data": {"entries": [
                    {"lane": lane, "exhibition_course": lane,
                     "start_time": lane / 100, "exhibition_time": 6.7 + lane / 100}
                    for lane in range(1, 7)
                ]},
            },
            "original_exhibition": {
                "status": "complete",
                "complete": True,
                "data": {"entries": [
                    {"lane": lane, "half_lap_time": 18 + lane / 10,
                     "turn_time": 4 + lane / 10, "straight_time": 7 + lane / 10}
                    for lane in range(1, 7)
                ]},
            },
        }
        value, first_hash = build_live_calculation_input(documents)
        self.assertEqual(len(value["boats"]), 6)
        self.assertEqual(len(first_hash), 64)
        self.assertEqual(value["actual_entry"], list(range(1, 7)))
        self.assertEqual(
            set(value["boats"][0]),
            {"lane", "national_avg_st", "local_avg_st",
             "exhibition_course", "start_time", "exhibition_time",
             "half_lap_time", "turn_time", "straight_time"},
        )
        self.assertEqual(
            set(value["weather"]),
            {"weather", "air_temperature", "water_temperature", "wind_direction",
             "wind_speed", "wave_height"},
        )

        documents["exhibition"]["data"]["entries"][0]["start_time"] = -0.03
        _, changed_hash = build_live_calculation_input(documents)
        self.assertNotEqual(first_hash, changed_hash)

        with self.assertRaisesRegex(RuntimeError, "validated realtime coefficients"):
            require_live_probability_calculation(value, first_hash)

        fallback = fallback_prediction(morning={
            **{position: {str(lane): 100 / 6 for lane in range(1, 7)}
               for position in ("win", "second", "third")},
            "predictionStage": "morning",
        }, documents=documents, input_hash=first_hash)
        self.assertEqual(fallback["predictionStage"], "morning")
        self.assertEqual(fallback["finalPredictionStatus"], "failed")
        self.assertEqual(fallback["predictionFailureReason"], FAILURE_REASON)
        self.assertFalse(fallback["probabilityFlow"]["realtimeApplied"])
        self.assertFalse(fallback["liveCalculation"]["executed"])
        self.assertEqual(fallback["liveInput"]["inputHash"], first_hash)


if __name__ == "__main__":
    unittest.main()
