from __future__ import annotations

import csv
import json
import math
import sys
import unittest
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engines" / "fukuoka_v1"
AUTOMATION_DIR = ROOT / "automation"
for directory in (ENGINE_DIR, AUTOMATION_DIR):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from lane1_fly_v1 import (  # noqa: E402
    COEFFICIENTS,
    calculate_base_fly,
    calculate_probability,
    predict_lane1_fly,
    tide60_indicator,
)
import run_fukuoka_v1 as runner  # noqa: E402


class TestFukuokaLane1FlyV1(unittest.TestCase):
    def test_coefficients_match_canonical_file(self) -> None:
        self.assertEqual(COEFFICIENTS["f1"]["prior_escape_probability"], 0.5335)
        self.assertEqual(COEFFICIENTS["f1"]["pseudo_starts"], 20)
        self.assertEqual(COEFFICIENTS["calibration"]["intercept"], -0.18942602852611862)
        self.assertEqual(COEFFICIENTS["calibration"]["f1_logit_coef"], 1.6178979044475232)
        self.assertEqual(COEFFICIENTS["calibration"]["tide60_coef"], 0.2798457516873573)

    def test_all_72_validation_rows_match_before_display_rounding(self) -> None:
        fixture = ROOT / "tests" / "fixtures" / "fukuoka_fly_candidate_validation_72r.csv"
        with fixture.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 72)
        errors = []
        for row in rows:
            actual = calculate_probability(float(row["f1"]), int(row["tide60"]))
            self.assertIsNotNone(actual)
            errors.append(abs(actual - float(row["f1_plus_tide60"])))
        self.assertLessEqual(max(errors), 1e-6)

    def test_f1_uses_exact_20_start_smoothing(self) -> None:
        actual = calculate_base_fly(70.0, 30)
        expected = 1.0 - (30 * 0.70 + 20 * 0.5335) / 50
        self.assertAlmostEqual(actual, expected, places=15)

    def test_tide_window_is_inclusive_at_both_boundaries(self) -> None:
        payload = {
            "tide": {
                "events": [
                    {"time": "11:00", "type": "干潮"},
                    {"time": "13:00", "type": "満潮"},
                ]
            }
        }
        indicator, detail = tide60_indicator(payload, "12:00")
        self.assertEqual(indicator, 1)
        self.assertEqual(len(detail["matchedEvents"]), 2)
        outside, outside_detail = tide60_indicator(payload, "14:01")
        self.assertEqual(outside, 0)
        self.assertEqual(outside_detail["matchedEvents"], [])

    def test_missing_inputs_emit_null_without_fallback(self) -> None:
        race = {
            "race": 1,
            "deadline": "12:00",
            "racers": [{"lane": 1, "boaters_escape_rate": None}],
        }
        result = predict_lane1_fly({"tide": {}}, race, "morning")
        self.assertIsNone(result["lane1FlyProbability"])
        self.assertEqual(result["lane1FlyLevel"], "算出不可")
        self.assertEqual(result["lane1FlyDetail"]["status"], "unavailable")
        self.assertIn("boaters_escape_rate", result["lane1FlyDetail"]["missingInputs"])
        self.assertIn("boaters_kimarite_starts", result["lane1FlyDetail"]["missingInputs"])
        self.assertIn("tide_events_or_deadline", result["lane1FlyDetail"]["missingInputs"])

    def test_morning_and_live_are_independent_from_existing_p1(self) -> None:
        payload = {"tide": {"events": [{"time": "12:00", "type": "干潮"}]}}
        race = {
            "race": 1,
            "deadline": "12:30",
            "racers": [
                {
                    "lane": 1,
                    "boaters_escape_rate": 61.5,
                    "boaters_kimarite_starts": 40,
                }
            ],
        }
        first = {"win": {"1": 10.0}, "sab": {"grade": "A"}, "tickets": ["1-2-3"]}
        second = deepcopy(first)
        second["win"]["1"] = 90.0
        protected_first = deepcopy(first)
        protected_second = deepcopy(second)
        runner.add_lane1_fly_probability(first, payload, race, "morning", {})
        runner.add_lane1_fly_probability(second, payload, race, "live", {})
        self.assertAlmostEqual(first["lane1FlyProbability"], second["lane1FlyProbability"], places=15)
        self.assertEqual(first["lane1FlyStage"], "morning")
        self.assertEqual(second["lane1FlyStage"], "live")
        for key in ("win", "sab", "tickets"):
            self.assertEqual(first[key], protected_first[key])
            self.assertEqual(second[key], protected_second[key])
        self.assertFalse(first["lane1FlyDetail"]["existingP1UsedForPrediction"])
        self.assertFalse(second["lane1FlyDetail"]["liveP1DeltaApplied"])

    def test_wind_attack_and_wall_are_record_only(self) -> None:
        payload = {"tide": {"events": [{"time": "12:00", "type": "満潮"}]}}
        race = {
            "deadline": "12:30",
            "racers": [
                {"lane": 1, "boaters_escape_rate": 60, "boaters_kimarite_starts": 20},
                {"lane": 2, "boaters_sashi_rate": 11, "boaters_makuri_rate": 7},
            ],
        }
        calm = predict_lane1_fly(payload, race, "live", {"wind_direction": "N", "wind_speed": 1})
        windy_race = deepcopy(race)
        windy_race["racers"][1]["boaters_makuri_rate"] = 99
        windy = predict_lane1_fly(payload, windy_race, "live", {"wind_direction": "S", "wind_speed": 20})
        self.assertEqual(calm["lane1FlyProbability"], windy["lane1FlyProbability"])
        self.assertFalse(calm["lane1FlyDetail"]["recordOnly"]["appliedToProbability"])
        self.assertNotEqual(
            calm["lane1FlyDetail"]["recordOnly"],
            windy["lane1FlyDetail"]["recordOnly"],
        )

    def test_addon_does_not_change_existing_fukuoka_prediction_domains(self) -> None:
        day = "2026-09-03"
        source = json.loads(
            (ROOT / "data" / "venues" / "fukuoka" / "20260903.json").read_text(
                encoding="utf-8"
            )
        )
        source["preds"] = {}
        for race in source["races"]:
            for key in (
                "prediction",
                "predictionPre",
                "predictionFinal",
                "predictionCurrentV1",
                "predictionCurrentV1Pre",
                "predictionCurrentV1Final",
            ):
                race.pop(key, None)
        live_root = ROOT / "data" / "live" / day / "fukuoka"
        with mock.patch.object(runner, "predict_lane1_fly", return_value={}):
            baseline = runner.apply_predictions(deepcopy(source), day, "preliminary", live_root)
        actual = runner.apply_predictions(deepcopy(source), day, "preliminary", live_root)
        fly_keys = {
            "lane1FlyProbability",
            "lane1FlyLevel",
            "lane1FlyStage",
            "lane1FlyDetail",
        }
        for baseline_race, actual_race in zip(baseline["races"], actual["races"]):
            expected = deepcopy(baseline_race["prediction"])
            observed = deepcopy(actual_race["prediction"])
            for key in fly_keys:
                expected.pop(key, None)
                observed.pop(key, None)
            self.assertEqual(observed, expected)

    def test_non_fukuoka_payload_is_rejected_without_mutation(self) -> None:
        payload = {"venueId": "tokoname", "date": "2026-09-03", "races": []}
        before = deepcopy(payload)
        with self.assertRaisesRegex(RuntimeError, "fukuoka_payload_identity_invalid"):
            runner.apply_predictions(payload, "2026-09-03", "preliminary", ROOT)
        self.assertEqual(payload, before)

    def test_latest_matches_latest_valid_dated_json(self) -> None:
        venue_dir = ROOT / "data" / "venues" / "fukuoka"
        latest = json.loads((venue_dir / "latest.json").read_text(encoding="utf-8"))

        dated_payloads = []
        for path in venue_dir.glob("*.json"):
            try:
                dated = datetime.strptime(path.stem, "%Y%m%d").date()
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, json.JSONDecodeError):
                continue
            dated_payloads.append((dated, payload))

        self.assertTrue(dated_payloads)
        _, dated = max(dated_payloads, key=lambda item: item[0])
        self.assertEqual(latest, dated)

    def test_20261012_public_json_exposes_all_morning_and_live_probabilities(self) -> None:
        dated = json.loads(
            (ROOT / "data" / "venues" / "fukuoka" / "20261012.json").read_text(
                encoding="utf-8"
            )
        )
        stages = set()
        for race in dated["races"]:
            prediction = race["prediction"]
            probability = prediction["lane1FlyProbability"]
            self.assertIsInstance(probability, (int, float))
            self.assertGreaterEqual(probability, 0.0)
            self.assertLessEqual(probability, 100.0)
            self.assertEqual(
                prediction["lane1FlyDetail"]["model"],
                "fukuoka_fly_f1_tide60_candidate_20261011",
            )
            self.assertEqual(prediction["lane1FlyDetail"]["probability"], probability)
            self.assertFalse(prediction["lane1FlyDetail"]["oddsUsedForPrediction"])
            self.assertFalse(prediction["lane1FlyDetail"]["resultUsedForPrediction"])
            stages.add(prediction["lane1FlyStage"])
            self.assertEqual(len(prediction["win"]), 6)
            self.assertEqual(len(prediction["second"]), 6)
            self.assertEqual(len(prediction["third"]), 6)
            self.assertEqual(len(prediction["ai"]), 6)
            self.assertEqual(len(prediction["balance"]), 2)
            self.assertEqual(len(prediction["aiUpset"]), 2)
            self.assertIn("grade", prediction["SAB"])
            expected_fly_count = 0 if race["race"] == 3 else 10
            self.assertEqual(
                len((prediction.get("flyPrediction") or {}).get("tickets") or []),
                expected_fly_count,
            )
            preliminary = race["predictionPre"]
            self.assertIsInstance(preliminary["lane1FlyProbability"], (int, float))
            self.assertEqual(preliminary["lane1FlyStage"], "morning")
            final = race.get("predictionFinal")
            if isinstance(final, dict):
                self.assertIsInstance(final["lane1FlyProbability"], (int, float))
                self.assertEqual(final["lane1FlyStage"], "live")
        self.assertEqual(stages, {"morning", "live"})


if __name__ == "__main__":
    unittest.main()
