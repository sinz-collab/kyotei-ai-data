from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "toda_v5"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from automation.apply_toda_live_v5 import apply_toda_live_review
from toda_fly_prediction_v5 import (
    BASE_FLY_WEIGHT,
    CURRENT_P1_WEIGHT,
    FLY_THRESHOLD,
    SIGMOID_INTERCEPT,
    SIGMOID_SLOPE,
    TODA_LANE1_ESCAPE_BASELINE,
    build_fly_prediction,
)
from toda_live_review_v5 import apply_live_review


class TodaFlyPredictionV5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = REPO_ROOT / "data" / "venues" / "toda" / "20261006.json"
        cls.payload = json.loads(path.read_text(encoding="utf-8"))

    def _reviewed(self, race_no=1):
        prediction = copy.deepcopy(self.payload["preds"][str(race_no)])
        racers = prediction["modelInputs"]["racers"]
        return prediction, racers

    def test_constants_are_fixed_to_production_specification(self):
        self.assertEqual(BASE_FLY_WEIGHT, 0.65)
        self.assertEqual(CURRENT_P1_WEIGHT, 0.35)
        self.assertEqual(SIGMOID_INTERCEPT, -4.089776194435157)
        self.assertEqual(SIGMOID_SLOPE, 0.06374919859354038)
        self.assertEqual(FLY_THRESHOLD, 50.0)

    def test_race_one_formula_uses_final_current_p1_and_empirical_bayes(self):
        prediction, racers = self._reviewed(1)
        fly = build_fly_prediction(prediction, racers)
        lane1 = next(row for row in racers if int(row["lane"]) == 1)
        sample_n = float(lane1["boaters_kimarite_starts"])
        weight = sample_n / (sample_n + 20.0)
        adjusted_escape = (
            weight * float(lane1["boaters_escape_rate"])
            + (1.0 - weight) * TODA_LANE1_ESCAPE_BASELINE
        )
        expected_raw = min(95.0, max(
            5.0,
            0.65 * (100.0 - adjusted_escape)
            + 0.35 * (100.0 - float(prediction["win"]["1"]))
            + fly["attackAdj"]
            + fly["resistanceAdj"],
        ))
        expected_probability = 100.0 / (
            1.0 + math.exp(-(SIGMOID_INTERCEPT + SIGMOID_SLOPE * expected_raw))
        )
        self.assertEqual(fly["currentP1"], prediction["win"]["1"])
        self.assertAlmostEqual(fly["adjustedEscape"], adjusted_escape, places=6)
        self.assertAlmostEqual(fly["rawFlyScore"], expected_raw, places=6)
        self.assertEqual(fly["probability"], round(expected_probability, 1))
        self.assertFalse(fly["oddsUsed"])

    def test_zero_escape_rate_is_valid_and_missing_personal_data_falls_back(self):
        prediction, racers = self._reviewed(1)
        fly = build_fly_prediction(prediction, racers)
        self.assertEqual(fly["personalEscape"], 0.0)
        self.assertFalse(fly["personalEscapeFallback"])

        missing = copy.deepcopy(racers)
        missing[0].pop("boaters_escape_rate", None)
        fallback = build_fly_prediction(prediction, missing)
        self.assertTrue(fallback["personalEscapeFallback"])
        self.assertAlmostEqual(fallback["baseFly"], 100.0 - TODA_LANE1_ESCAPE_BASELINE, places=6)

    def test_missing_attack_rate_is_not_guessed(self):
        prediction, racers = self._reviewed(1)
        missing = copy.deepcopy(racers)
        next(row for row in missing if int(row["lane"]) == 2).pop("boaters_sashi_rate", None)
        with self.assertRaisesRegex(RuntimeError, "toda_fly_attack_rate_missing"):
            build_fly_prediction(prediction, missing)

    def test_fly_tickets_are_ten_unique_conditional_tickets(self):
        prediction, racers = self._reviewed(1)
        fly = build_fly_prediction(prediction, racers)
        tickets = fly["tickets"]
        self.assertTrue(fly["isFly"])
        self.assertEqual(len(tickets), 10)
        self.assertEqual(len({row["combo"] for row in tickets}), 10)
        self.assertTrue(all(not row["combo"].startswith("1-") for row in tickets))
        self.assertEqual(sum(row["role"] == "Main HEAD" for row in tickets), 5)
        self.assertEqual(sum(row["role"] == "Second HEAD" for row in tickets), 3)
        self.assertEqual(sum(row["role"] == "展開連動" for row in tickets), 2)
        main = [row for row in tickets if row["role"] == "Main HEAD"]
        second = [row for row in tickets if row["role"] == "Second HEAD"]
        self.assertEqual(sum(row["pattern"] == "lane1Second" for row in main), 2)
        self.assertEqual(sum(row["pattern"] == "lane1Third" for row in main), 1)
        self.assertEqual(sum(row["pattern"] == "lane1Out" for row in main), 2)
        self.assertEqual(sum(row["pattern"] == "lane1Second" for row in second), 2)
        self.assertEqual(sum(row["pattern"] == "lane1Third" for row in second), 1)

    def test_non_fly_has_no_recommended_tickets(self):
        prediction, racers = self._reviewed(1)
        prediction["win"]["1"] = 100.0
        for row in racers:
            if int(row["lane"]) == 1:
                row["boaters_escape_rate"] = 100.0
                row["boaters_kimarite_starts"] = 100000
                row["boaters_sashare_rate"] = 0.0
                row["boaters_makurare_rate"] = 0.0
                row["boaters_makurare_zashi_rate"] = 0.0
        prediction["scenarios"] = [
            row for row in prediction["scenarios"]
            if row.get("id") != "CURRENT_FORM_INSIDE_RESISTANCE"
        ]
        fly = build_fly_prediction(prediction, racers)
        self.assertFalse(fly["isFly"])
        self.assertEqual(fly["tickets"], [])

    def test_live_connector_adds_fly_after_review_without_changing_normal_output(self):
        race_no = 1
        live_root = REPO_ROOT / "data" / "live" / "2026-10-06" / "toda" / "01"
        source = copy.deepcopy(self.payload)
        expected = copy.deepcopy(source["preds"][str(race_no)])
        documents = {
            "direct": json.loads((live_root / "direct.json").read_text(encoding="utf-8")),
            "exhibition": json.loads((live_root / "exhibition.json").read_text(encoding="utf-8")),
        }
        original_path = live_root / "original_exhibition.json"
        if original_path.is_file():
            documents["original_exhibition"] = json.loads(original_path.read_text(encoding="utf-8"))
        self.assertTrue(apply_live_review(expected, documents))

        actual_payload = apply_toda_live_review(source, "2026-10-06", race_no, live_root)
        actual = actual_payload["preds"][str(race_no)]
        for key in ("win", "second", "third", "sab", "ai", "aiUpset", "tickets", "scenarios"):
            self.assertEqual(actual[key], expected[key], key)
        self.assertEqual(actual["flyPrediction"]["status"], "final")
        self.assertEqual(
            next(row for row in actual_payload["races"] if row["race"] == race_no)["prediction"]["flyPrediction"],
            actual["flyPrediction"],
        )

    def test_other_venues_are_rejected_by_toda_connector(self):
        payload = copy.deepcopy(self.payload)
        payload["venueId"] = "biwako"
        with self.assertRaisesRegex(RuntimeError, "toda_payload_identity_invalid"):
            apply_toda_live_review(
                payload,
                "2026-10-06",
                1,
                REPO_ROOT / "data" / "live" / "2026-10-06" / "toda" / "01",
            )


if __name__ == "__main__":
    unittest.main()
