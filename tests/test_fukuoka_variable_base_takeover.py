from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "engines" / "fukuoka_restore_candidate_v1"))
sys.path.insert(0, str(ROOT / "tests"))

import fukuoka_restore_candidate_v1 as restore  # noqa: E402
import run_fukuoka_v1 as runner  # noqa: E402
from replay_fukuoka_variable_base_takeover import replay  # noqa: E402


def feature(lane: int) -> dict:
    return {
        "actual_course": lane,
        "p1": restore.BASE_WIN_PERCENT[lane],
        "p2": 20.0,
        "p3": 20.0,
        "national": 5.0,
        "local": 5.0,
        "local_st": 0.18,
        "sashi": 0.0,
        "makuri": 0.0,
        "makuri_sashi": 0.0,
        "escape": 60.0,
        "motor_top2": 40.0,
        "motor_top3": 60.0,
        "motor_rate_top2": 40.0,
        "motor_rate_top3": 60.0,
        "motor_trend": "flat",
        "start_rank": 99,
        "exhibition_time": 99.0,
        "exhibition_time_rank": 99,
        "turn": 99.0,
        "turn_rank": 99,
        "straight": 99.0,
        "straight_rank": 99,
        "lap": 99.0,
        "lap_rank": 99,
        "sum": 99.0,
        "sum_rank": 99,
        "recent_finishes": [],
    }


class TestFukuokaVariableBaseTakeover(unittest.TestCase):
    def features(self) -> dict[int, dict]:
        return {lane: feature(lane) for lane in range(1, 7)}

    def test_weakness_score_calculation_and_clamp(self) -> None:
        features = self.features()
        features[1].update({
            "escape": 25.0,
            "motor_rate_top2": 10.0,
            "recent_finishes": [5, 5, 5],
            "exhibition_time_rank": 5,
            "start_rank": 5,
            "lap_rank": 5,
            "turn_rank": 5,
        })
        score, static, live = restore.calculate_weakness_score(features, 6.0)
        self.assertEqual(static, 9.5)
        self.assertEqual(live, 8.0)
        self.assertEqual(score, 16.0)

    def test_non_linear_base_cut_interpolation(self) -> None:
        self.assertEqual(restore.interpolate_base_cut(0), 0.0)
        self.assertEqual(restore.interpolate_base_cut(12), 16.0)
        self.assertEqual(restore.interpolate_base_cut(16), 24.0)
        self.assertEqual(restore.interpolate_base_cut(99), 24.0)

    def test_head_score_uses_all_fixed_components(self) -> None:
        features = self.features()
        features[1].update({
            "national": 5.0, "local": 5.0,
            "motor_rate_top2": 30.0, "motor_rate_top3": 45.0,
        })
        features[2].update({
            "national": 5.8, "local": 5.8,
            "motor_rate_top2": 41.0, "motor_rate_top3": 58.0,
            "recent_finishes": [1, 2, 5], "start_rank": 2,
            "exhibition_time_rank": 2, "lap_rank": 2, "turn_rank": 2,
            "sashi": 25.0,
        })
        scores = restore.calculate_head_scores(features, {1: 50, 2: 18, 3: 10, 4: 10, 5: 5, 6: 5})
        self.assertEqual(scores[2], 23.0)

    def assert_distribution(self, gap: float, expected: tuple[float, float, float]) -> None:
        weights = restore.redistribution_weights(gap)
        self.assertEqual(weights, expected)
        values = {lane: 0.0 for lane in range(1, 7)}
        redistributed = restore.redistribute_base_cut(
            values, 20.0, {2: 1.0, 3: 1.0, 4: 1.0}, [2, 3, 4], weights,
        )
        self.assertEqual(tuple(redistributed[lane] for lane in (2, 3, 4)), tuple(20.0 * x for x in expected))

    def test_redistribution_80_15_5(self) -> None:
        self.assert_distribution(4.0, (0.80, 0.15, 0.05))

    def test_redistribution_65_25_10(self) -> None:
        self.assert_distribution(2.5, (0.65, 0.25, 0.10))

    def test_redistribution_50_35_15(self) -> None:
        self.assert_distribution(2.49, (0.50, 0.35, 0.15))

    def variable_result(self, scores: dict[int, float], weakness: float):
        features = self.features()
        morning = dict(restore.BASE_WIN_PERCENT)
        with patch.object(restore, "calculate_head_scores", return_value=scores), patch.object(
            restore, "calculate_weakness_score", return_value=(weakness, 0.0, 0.0),
        ):
            return restore.build_variable_p1(features, morning, current_win=morning)

    def test_takeover_activates_on_all_boundaries(self) -> None:
        probabilities, audit = self.variable_result({2: 12.0, 3: 8.0, 4: 4.0}, 12.0)
        self.assertTrue(audit["takeover"])
        self.assertEqual(audit["head_dominance"], 6.0)
        self.assertGreater(probabilities[2], probabilities[1])

    def test_takeover_does_not_activate_below_dominance_boundary(self) -> None:
        _, audit = self.variable_result({2: 12.0, 3: 8.0, 4: 4.002}, 12.0)
        self.assertAlmostEqual(audit["head_dominance"], 5.999)
        self.assertFalse(audit["takeover"])

    def test_p1_normalizes_to_100_percent(self) -> None:
        probabilities, _ = self.variable_result({2: 12.0, 3: 8.0, 4: 4.0}, 12.0)
        self.assertAlmostEqual(sum(probabilities.values()) * 100.0, 100.0)

    def test_odds_and_result_are_not_prediction_inputs(self) -> None:
        day = "2026-09-27"
        path = ROOT / "data" / "venues" / "fukuoka" / "20260927.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        race = payload["races"][9]
        documents = runner.live_documents(
            ROOT / "data" / "live" / day / "fukuoka" / "10", day, 10,
        )
        baseline = runner.build_engine_input(payload, race, documents)
        injected = deepcopy(race)
        injected["odds"] = {"3-4-1": 1.0}
        injected["result"] = {"order": [6, 5, 4]}
        self.assertEqual(baseline, runner.build_engine_input(payload, injected, documents))

    def test_non_fukuoka_payload_is_rejected_without_mutation(self) -> None:
        payload = {"date": "2026-09-27", "venueId": "omura", "races": []}
        original = deepcopy(payload)
        with self.assertRaisesRegex(RuntimeError, "fukuoka_payload_identity_invalid"):
            runner.apply_predictions(payload, "2026-09-27", "final", Path("unused"))
        self.assertEqual(payload, original)

    def test_october_2_entry_changes_use_actual_course_in_restore(self) -> None:
        fixtures = {
            5: ([1, 5, 2, 3, 4, 6], 5),
            6: ([1, 6, 2, 3, 4, 5], 6),
        }
        for race_no, (actual_entry, course2_lane) in fixtures.items():
            with self.subTest(race=race_no):
                features = self.features()
                for course, lane in enumerate(actual_entry, 1):
                    features[lane]["actual_course"] = course
                features[course2_lane]["sashi"] = 25.0
                scores = restore.calculate_head_scores(
                    features, dict(restore.BASE_WIN_PERCENT),
                )
                self.assertEqual(
                    set(scores), set(actual_entry[1:4]),
                )
                self.assertIn(course2_lane, scores)
                self.assertEqual(
                    restore.course_attack_score(
                        features[course2_lane]["actual_course"], 25.0, 0.0, 0.0,
                    ),
                    4.0,
                )
                _, audit = restore.build_variable_p1(
                    features,
                    dict(restore.BASE_WIN_PERCENT),
                    current_win=dict(restore.BASE_WIN_PERCENT),
                )
                self.assertTrue(audit["entry_changed"])
                self.assertEqual(audit["actual_course_by_lane"][course2_lane], 2)

    def test_october_2_entry_change_flag_preserves_actual_entry(self) -> None:
        fixtures = {
            5: [1, 5, 2, 3, 4, 6],
            6: [1, 6, 2, 3, 4, 5],
        }
        for race_no, actual_entry in fixtures.items():
            with self.subTest(race=race_no):
                documents = {
                    "direct": {"data": {"actual_entry": list(actual_entry), "racers": []}},
                    "exhibition": {"data": {"entries": [], "slit_source": []}},
                    "original_exhibition": {"data": {"entries": []}},
                }
                race_input = {
                    "boats": [
                        {"lane": lane, "entry_course": lane}
                        for lane in range(1, 7)
                    ]
                }
                runner.apply_live_input(race_input, documents)
                self.assertEqual(documents["direct"]["data"]["actual_entry"], actual_entry)
                self.assertTrue(documents["direct"]["data"]["entry_changed"])
                self.assertEqual(
                    next(
                        boat["actual_course"]
                        for boat in race_input["boats"]
                        if boat["lane"] == actual_entry[1]
                    ),
                    2,
                )
                current = runner.FukuokaPredictionEngineV10().predict(race_input)
                self.assertTrue(current["diagnostics"]["entry_changed"])

    def test_identity_entry_keeps_existing_course_evaluation(self) -> None:
        documents = {
            "direct": {
                "data": {
                    "actual_entry": list(range(1, 7)),
                    "entry_changed": False,
                    "racers": [],
                }
            },
            "exhibition": {"data": {"entries": [], "slit_source": []}},
            "original_exhibition": {"data": {"entries": []}},
        }
        race_input = {
            "boats": [
                {"lane": lane, "entry_course": lane}
                for lane in range(1, 7)
            ]
        }
        runner.apply_live_input(race_input, documents)
        self.assertFalse(documents["direct"]["data"]["entry_changed"])
        self.assertEqual(
            [boat["actual_course"] for boat in race_input["boats"]],
            list(range(1, 7)),
        )
        current = runner.FukuokaPredictionEngineV10().predict(race_input)
        self.assertFalse(current["diagnostics"]["entry_changed"])

    def test_36_race_regression_and_required_boundaries(self) -> None:
        reports = {day: replay(day) for day in ("2026-09-25", "2026-09-26", "2026-09-27")}
        self.assertEqual([reports[day]["hits"] for day in reports], [8, 7, 8])
        self.assertEqual(sum(report["hits"] for report in reports.values()), 23)
        self.assertEqual(reports["2026-09-25"]["takeovers"], [2])
        self.assertEqual(reports["2026-09-27"]["takeovers"], [10])
        self.assertEqual(reports["2026-09-25"]["races"][1]["top1"], 2)
        self.assertEqual(reports["2026-09-27"]["races"][4]["top1"], 1)
        race_26_10 = reports["2026-09-26"]["races"][9]
        self.assertEqual(race_26_10["headScores"], {"2": 13.5, "3": 15.0, "4": 19.0})
        self.assertEqual(race_26_10["headDominance"], 4.75)
        self.assertFalse(race_26_10["takeover"])
        race_27_10 = reports["2026-09-27"]["races"][9]
        self.assertTrue(race_27_10["takeover"])
        self.assertEqual(race_27_10["top1"], 3)
        self.assertAlmostEqual(race_27_10["win"]["3"], 34.9, delta=0.1)
        self.assertAlmostEqual(race_27_10["win"]["1"], 28.3, delta=0.1)


if __name__ == "__main__":
    unittest.main()
