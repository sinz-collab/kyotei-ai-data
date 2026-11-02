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
    build_fly_tickets,
)
from toda_live_review_v5 import apply_live_review
from toda_ticket_engine_v5 import build_tickets, conditional_third


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

    def test_missing_attack_rate_zeros_only_its_matchup_component(self):
        prediction, racers = self._reviewed(1)
        expected = build_fly_prediction(prediction, racers)
        missing = copy.deepcopy(racers)
        next(row for row in missing if int(row["lane"]) == 2).pop("boaters_sashi_rate", None)
        actual = build_fly_prediction(prediction, missing)

        expected_components = {
            row["id"]: row for row in expected["attackMatchupComponents"]
        }
        actual_components = {
            row["id"]: row for row in actual["attackMatchupComponents"]
        }
        missing_component = actual_components["1_sashare_x_2_sashi"]
        self.assertEqual(missing_component["attackRate"], 0.0)
        self.assertEqual(missing_component["matchup"], 0.0)
        for component_id in actual_components.keys() - {"1_sashare_x_2_sashi"}:
            self.assertEqual(actual_components[component_id], expected_components[component_id])

    def test_fly_tickets_are_verified_eight_plus_two(self):
        prediction, racers = self._reviewed(1)
        fly = build_fly_prediction(prediction, racers)
        tickets = fly["tickets"]
        self.assertTrue(fly["isFly"])
        self.assertEqual(len(tickets), 10)
        self.assertEqual(len({row["combo"] for row in tickets}), 10)
        self.assertTrue(all(not row["combo"].startswith("1-") for row in tickets))
        self.assertEqual(sum(row["role"] == "条件付き確率上位" for row in tickets), 8)
        self.assertEqual(sum(row["role"] == "展開シナリオ" for row in tickets), 2)
        self.assertEqual(fly["ticketMethod"], "conditional_top8_plus_scenario_top2")
        self.assertEqual(fly["morningWeight"], 0.8)
        self.assertEqual(fly["liveWeight"], 0.2)
        top8 = tickets[:8]
        self.assertEqual(
            [row["prob"] for row in top8],
            sorted((row["prob"] for row in top8), reverse=True),
        )
        scenario = tickets[8:]
        self.assertTrue(all(row["scenarioScore"] >= 0 for row in scenario))
        self.assertTrue(all("scenarioEstablishment" in row for row in scenario))

    def test_normal_tickets_keep_fixed_ten_role_contract(self):
        prediction, _ = self._reviewed(1)
        tickets = build_tickets(
            prediction["win"],
            prediction["secondByHead"],
            prediction["thirdByHead"],
            prediction["scenarios"],
            prediction["sab"],
        )
        roles = [row["role"] for row in tickets]
        self.assertEqual(len(tickets), 10)
        self.assertEqual(len({row["combo"] for row in tickets}), 10)
        self.assertEqual(roles.count("本線"), 6)
        self.assertEqual(roles.count("2着ズレ"), 1)
        self.assertEqual(roles.count("3着ズレ"), 1)
        self.assertEqual(roles.count("シナリオ穴"), 2)

    def test_fly_tickets_allow_normal_overlap(self):
        prediction, racers = self._reviewed(1)
        expected = build_fly_prediction(prediction, racers)
        prediction["ai"] = [{"combo": expected["tickets"][0]["combo"]}]
        actual = build_fly_prediction(prediction, racers)
        self.assertEqual(actual["tickets"], expected["tickets"])
        self.assertIn(prediction["ai"][0]["combo"], {row["combo"] for row in actual["tickets"]})

    def test_morning_uses_one_hundred_percent_morning_values(self):
        prediction, _ = self._reviewed(1)
        morning = copy.deepcopy(prediction)
        baseline = morning.pop("_baseline")
        for key in ("win", "secondByHead", "thirdByHead", "scenarios"):
            morning[key] = copy.deepcopy(baseline[key])
        tickets = build_fly_tickets(morning, status="pre")
        self.assertEqual(tickets["morningWeight"], 1.0)
        self.assertEqual(tickets["liveWeight"], 0.0)
        self.assertEqual(len(tickets["tickets"]), 10)

    def test_live_ticket_probability_is_morning_eighty_live_twenty(self):
        prediction, _ = self._reviewed(1)
        result = build_fly_tickets(prediction, status="final")
        ticket = result["tickets"][0]
        head, second, third = map(int, ticket["combo"].split("-"))

        def probability(source):
            third_map = conditional_third(head, second, source["thirdByHead"])
            return (
                float(source["win"][str(head)])
                * float(source["secondByHead"][str(head)][str(second)])
                * float(third_map[str(third)])
                / 10000.0
            )

        expected = 0.8 * probability(prediction["_baseline"]) + 0.2 * probability(prediction)
        self.assertEqual(ticket["prob"], round(expected, 4))

    def test_live_scenario_score_is_morning_eighty_live_twenty(self):
        prediction, _ = self._reviewed(1)
        result = build_fly_tickets(prediction, status="final")
        ticket = result["tickets"][8]
        head, second, third = map(int, ticket["combo"].split("-"))

        def scenario_value(source):
            scenario = next(
                (row for row in source.get("scenarios") or [] if int(row.get("head") or 0) == head),
                {},
            )
            establishment = float(scenario.get("attackEstablishment", scenario.get("weight", 0.0)))
            third_map = conditional_third(head, second, source["thirdByHead"])
            conditional = (
                float(source["secondByHead"][str(head)][str(second)])
                * float(third_map[str(third)])
                / 100.0
            )
            return establishment * conditional

        expected = 0.8 * scenario_value(prediction["_baseline"]) + 0.2 * scenario_value(prediction)
        self.assertEqual(ticket["scenarioScore"], round(expected, 6))

    def test_odds_and_results_do_not_change_fly_tickets(self):
        prediction, racers = self._reviewed(1)
        expected = build_fly_prediction(prediction, racers)["tickets"]
        changed = copy.deepcopy(prediction)
        changed["odds"] = {"2-1-3": 9999.9}
        changed["result"] = {"order": "6-5-4", "payout": 999999}
        self.assertEqual(build_fly_prediction(changed, racers)["tickets"], expected)

    def test_non_fly_still_has_ten_recommended_tickets(self):
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
        self.assertEqual(fly["judgement"], "逃げ優勢")
        self.assertEqual(len(fly["tickets"]), 10)

    def test_probability_boundary_never_suppresses_fly_tickets(self):
        prediction, racers = self._reviewed(1)
        prediction["scenarios"] = []
        lane1 = next(row for row in racers if int(row["lane"]) == 1)
        lane1["boaters_escape_rate"] = 30.0
        lane1["boaters_kimarite_starts"] = 1_000_000_000.0
        for key in ("boaters_sashare_rate", "boaters_makurare_rate", "boaters_makurare_zashi_rate"):
            lane1[key] = 0.0
        for row in racers:
            if int(row["lane"]) in (2, 3, 4):
                for key in ("boaters_sashi_rate", "boaters_makuri_rate", "boaters_makuri_sashi_rate"):
                    row[key] = 0.0

        base_fly = build_fly_prediction(prediction, racers)["baseFly"]
        for expected in (25.0, 49.0, 50.0, 65.0):
            formula_probability = expected + (0.01 if expected == FLY_THRESHOLD else 0.0)
            target_raw = (
                math.log((formula_probability / 100.0) / (1.0 - formula_probability / 100.0))
                - SIGMOID_INTERCEPT
            ) / SIGMOID_SLOPE
            prediction["win"]["1"] = 100.0 - (
                target_raw - BASE_FLY_WEIGHT * base_fly
            ) / CURRENT_P1_WEIGHT
            fly = build_fly_prediction(prediction, racers)
            self.assertEqual(fly["probability"], expected)
            self.assertEqual(len(fly["tickets"]), 10)
            self.assertEqual(
                fly["judgement"],
                "飛び優勢" if expected >= FLY_THRESHOLD else "逃げ優勢",
            )

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
        self.assertEqual(len(actual["flyPrediction"]["tickets"]), 10)
        self.assertEqual(actual["flyPrediction"]["morningWeight"], 0.8)
        self.assertEqual(actual["flyPrediction"]["liveWeight"], 0.2)
        self.assertEqual(
            next(row for row in actual_payload["races"] if row["race"] == race_no)["prediction"]["flyPrediction"],
            actual["flyPrediction"],
        )

    def test_october_replay_preserves_non_ticket_outputs(self):
        checked = 0
        for path in sorted((REPO_ROOT / "data" / "venues" / "toda").glob("202610*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            for prediction in (payload.get("preds") or {}).values():
                racers = (prediction.get("modelInputs") or {}).get("racers") or []
                if len(racers) != 6 or not prediction.get("flyPrediction"):
                    continue
                frozen = copy.deepcopy({
                    key: prediction.get(key)
                    for key in ("win", "second", "third", "sab", "ai", "aiUpset", "scenarios")
                })
                previous_probability = prediction["flyPrediction"]["probability"]
                fly = build_fly_prediction(prediction, racers, status=prediction["flyPrediction"].get("status", "final"))
                self.assertEqual(fly["probability"], previous_probability, path.name)
                self.assertEqual(len(fly["tickets"]), 10, path.name)
                self.assertEqual(len({row["combo"] for row in fly["tickets"]}), 10, path.name)
                self.assertTrue(all(not row["combo"].startswith("1-") for row in fly["tickets"]), path.name)
                self.assertEqual(
                    {key: prediction.get(key) for key in frozen},
                    frozen,
                    path.name,
                )
                checked += 1
        self.assertGreater(checked, 0)

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
