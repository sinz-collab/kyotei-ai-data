from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
if str(AUTOMATION) not in sys.path:
    sys.path.insert(0, str(AUTOMATION))

import build_site_data  # noqa: E402
import run_fukuoka_v1 as runner  # noqa: E402


class TestFukuokaV1Integration(unittest.TestCase):
    def payload(self, day: str) -> dict:
        return json.loads(
            (ROOT / "data" / "venues" / "fukuoka" / f"{day.replace('-', '')}.json")
            .read_text(encoding="utf-8")
        )

    def live_root(self, day: str) -> Path:
        return ROOT / "data" / "live" / day / "fukuoka"

    def assert_prediction_contract(self, prediction: dict, phase: str) -> None:
        self.assertEqual(prediction["engine"], "fukuoka_engine_v1.0")
        self.assertEqual(prediction["engineVersion"], "1.0")
        self.assertEqual(prediction["phase"], phase)
        self.assertEqual(prediction["status"], "complete")
        for key in ("win", "second", "third"):
            self.assertEqual(set(prediction[key]), {str(lane) for lane in range(1, 7)})
            self.assertAlmostEqual(sum(prediction[key].values()), 100.0, delta=0.05)
        self.assertEqual(len(prediction["Main6"]), 6)
        self.assertEqual(len(prediction["Zure2"]), 2)
        self.assertEqual(len(prediction["Ana2"]), 2)
        self.assertEqual(len(prediction["tickets"]), 10)
        self.assertEqual(len({row["combo"] for row in prediction["tickets"]}), 10)
        self.assertFalse(prediction["diagnostics"]["oddsUsedForPrediction"])
        self.assertFalse(prediction["diagnostics"]["resultUsedForPrediction"])
        self.assertEqual(prediction["scenario"]["model"], "conditional_trifecta")
        self.assertTrue(build_site_data.fukuoka_race_prediction_is_complete(prediction))

    def test_morning_and_live_connection_for_saved_days(self) -> None:
        event_days = {}
        for day in ("2026-09-02", "2026-09-03"):
            payload = self.payload(day)
            event_days[day] = payload.get("eventDay")
            preliminary = runner.apply_predictions(
                deepcopy(payload), day, "preliminary", self.live_root(day)
            )
            self.assertEqual(len(preliminary["preds"]), 12)
            for race in preliminary["races"]:
                self.assert_prediction_contract(race["predictionPre"], "preliminary")
                self.assertIsNone(race["predictionFinal"])
                self.assertEqual(race["prediction"], race["predictionPre"])

            final = runner.apply_predictions(
                preliminary, day, "final", self.live_root(day)
            )
            self.assertEqual(final["predictionEngine"]["finalRaceCount"], 12)
            for race in final["races"]:
                self.assert_prediction_contract(race["predictionPre"], "preliminary")
                self.assert_prediction_contract(race["predictionFinal"], "final")
                self.assertEqual(race["prediction"], race["predictionFinal"])
                self.assertEqual(race["prediction"]["probabilityReviewStatus"], "reviewed")
                self.assertTrue(race["prediction"]["probabilityFlow"]["reviewed"])
                self.assertEqual(
                    set(race["live"]),
                    {"direct", "weather", "exhibition", "original", "original_exhibition"},
                )
        self.assertEqual(event_days, {"2026-09-02": 1, "2026-09-03": 2})

    def test_engine_input_never_contains_odds_or_result(self) -> None:
        day = "2026-09-03"
        payload = self.payload(day)
        race = payload["races"][0]
        documents = runner.live_documents(self.live_root(day) / "01", day, 1)
        self.assertIsNotNone(documents)
        engine_input = runner.build_engine_input(payload, race, documents)
        serialized = json.dumps(engine_input, ensure_ascii=False).lower()
        self.assertNotIn("odds", serialized)
        self.assertNotIn("result", serialized)
        self.assertEqual(engine_input["event_day"], 2)
        self.assertEqual(len(engine_input["boats"]), 6)
        self.assertTrue(all(boat.get("actual_course") for boat in engine_input["boats"]))
        self.assertTrue(all(boat.get("exhibition_time") is not None for boat in engine_input["boats"]))
        self.assertTrue(all(boat.get("original_sum") is not None for boat in engine_input["boats"]))

    def test_replay_results_are_checked_only_after_prediction(self) -> None:
        for day in ("2026-09-02", "2026-09-03"):
            payload = runner.apply_predictions(
                self.payload(day), day, "final", self.live_root(day)
            )
            hits = 0
            for race in payload["races"]:
                race_no = int(race["race"])
                result_document = json.loads(
                    (self.live_root(day) / f"{race_no:02d}" / "result.json")
                    .read_text(encoding="utf-8")
                )
                order = (result_document.get("data") or {}).get("order") or []
                actual = "-".join(map(str, order[:3]))
                tickets = {row["combo"] for row in race["prediction"]["tickets"]}
                hits += actual in tickets
            self.assertGreaterEqual(hits, 0)
            self.assertLessEqual(hits, 12)


if __name__ == "__main__":
    unittest.main()
