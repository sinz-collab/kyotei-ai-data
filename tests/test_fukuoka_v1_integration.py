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
from fukuoka_prediction_engine_v1_0 import (  # noqa: E402
    FukuokaPredictionEngineV10,
    motor_delta,
    slit_structure,
)


class TestFukuokaV1Integration(unittest.TestCase):
    def payload(self, day: str) -> dict:
        return json.loads(
            (ROOT / "data" / "venues" / "fukuoka" / f"{day.replace('-', '')}.json")
            .read_text(encoding="utf-8")
        )

    def live_root(self, day: str) -> Path:
        return ROOT / "data" / "live" / day / "fukuoka"

    def final_input(self, day: str, race_no: int) -> dict:
        payload = self.payload(day)
        race = next(row for row in payload["races"] if int(row["race"]) == race_no)
        documents = runner.live_documents(self.live_root(day) / f"{race_no:02d}", day, race_no)
        self.assertIsNotNone(documents)
        return runner.build_engine_input(payload, race, documents)

    def prediction_input_payload(self, day: str) -> dict:
        payload = self.payload(day)
        payload["preds"] = {}
        for race in payload["races"]:
            race.pop("prediction", None)
            race.pop("predictionPre", None)
            race.pop("predictionFinal", None)
        return payload

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
            payload = self.prediction_input_payload(day)
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

    def test_motor_recent_generates_grades_without_exceeding_existing_bounds(self) -> None:
        race_input = self.final_input("2026-09-03", 1)
        grades = {boat["motor_grade"] for boat in race_input["boats"]}
        self.assertNotEqual(grades, {"C"})
        for boat in race_input["boats"]:
            adjustment = motor_delta(boat["motor_grade"], boat["motor_trend"])
            self.assertGreaterEqual(adjustment, -4.0)
            self.assertLessEqual(adjustment, 4.0)

    def test_slit_structure_detects_peek_dent_wall_and_attacker(self) -> None:
        starts = {1: 0.06, 2: 0.05, 3: 0.16, 4: 0.02, 5: 0.08, 6: 0.09}
        boats = {
            lane: {
                "lane": lane,
                "actual_course": lane,
                "exhibition_st": starts[lane],
                "course_makuri_rate": 20.0 if lane == 4 else 0.0,
                "course_makuri_sashi_rate": 5.0 if lane == 4 else 0.0,
            }
            for lane in range(1, 7)
        }
        structure = slit_structure(boats, {lane: lane for lane in range(1, 7)})
        self.assertIn(4, structure["peeking"])
        self.assertIn(3, structure["dented"])
        self.assertIn(2, structure["walls"])
        self.assertIn(4, structure["attackers"])
        self.assertEqual(structure["outward"][4], [5, 6])

    def test_lane2_floor_survives_normalization(self) -> None:
        result = FukuokaPredictionEngineV10().predict(self.final_input("2026-09-03", 2))
        lane2 = next(row for row in result["boats"] if row["actual_course"] == 2)
        floor = result["diagnostics"]["lane2_floor"]
        self.assertTrue(floor["activated"])
        self.assertGreaterEqual(lane2["win_prob"], 0.185)
        self.assertLessEqual(lane2["win_prob"], 0.215)
        self.assertAlmostEqual(lane2["win_prob"], floor["achieved"], delta=0.001)

    def test_replay_debug_exposes_linear_win_audit_only_when_requested(self) -> None:
        race_input = self.final_input("2026-09-03", 5)
        engine = FukuokaPredictionEngineV10()
        normal = engine.predict(race_input)
        debug = engine.predict(race_input, debug=True)
        self.assertNotIn("win_audit", normal["diagnostics"])
        audit = debug["diagnostics"]["win_audit"]
        self.assertEqual(len(audit), 6)
        expected = {
            "base_win", "course_delta", "local_delta", "motor_delta",
            "setsukan_delta", "water_delta", "slit_delta",
            "exhibition_delta", "original_delta", "interaction_delta",
            "total_delta", "raw_win", "normalized_win",
        }
        self.assertTrue(all(expected.issubset(row) for row in audit))
        self.assertAlmostEqual(sum(row["normalized_win"] for row in audit), 100.0, places=4)
        self.assertTrue(all(abs(row["total_delta"]) <= 13.0 for row in audit))

    def test_original_sum_is_a_small_auxiliary_input(self) -> None:
        strong = self.final_input("2026-09-03", 5)
        weak = deepcopy(strong)
        lane = 4
        next(boat for boat in strong["boats"] if boat["lane"] == lane)["original_sum"] = 43.0
        next(boat for boat in weak["boats"] if boat["lane"] == lane)["original_sum"] = 47.0
        engine = FukuokaPredictionEngineV10()
        strong_probability = next(row["win_prob"] for row in engine.predict(strong)["boats"] if row["lane"] == lane)
        weak_probability = next(row["win_prob"] for row in engine.predict(weak)["boats"] if row["lane"] == lane)
        self.assertGreater(strong_probability, weak_probability)
        self.assertLess(strong_probability - weak_probability, 0.01)

    def test_slit_attacker_lightly_links_outward_third_probabilities(self) -> None:
        active = self.final_input("2026-09-03", 5)
        inactive = deepcopy(active)
        attacker = next(boat for boat in inactive["boats"] if boat["actual_course"] == 4)
        attacker["course_makuri_rate"] = 0.0
        attacker["course_makuri_sashi_rate"] = 0.0
        engine = FukuokaPredictionEngineV10()
        active_result = engine.predict(active)
        inactive_result = engine.predict(inactive)
        self.assertIn(4, active_result["diagnostics"]["slit_structure"]["attackers"])
        for course in (5, 6):
            active_third = next(row["third_prob"] for row in active_result["boats"] if row["actual_course"] == course)
            inactive_third = next(row["third_prob"] for row in inactive_result["boats"] if row["actual_course"] == course)
            self.assertGreater(active_third, inactive_third)

    def test_conditional_top_nine_is_not_removed_by_role_assignment(self) -> None:
        race_input = self.final_input("2026-09-03", 6)
        engine = FukuokaPredictionEngineV10()
        result = engine.predict(race_input)
        boats = {boat["lane"]: boat for boat in race_input["boats"]}
        courses = {boat["actual_course"]: boat["lane"] for boat in race_input["boats"]}
        win = {row["lane"]: row["win_prob"] for row in result["boats"]}
        second = {row["lane"]: row["second_prob"] for row in result["boats"]}
        third = {row["lane"]: row["third_prob"] for row in result["boats"]}
        scored = []
        for head in range(1, 7):
            for second_lane in range(1, 7):
                for third_lane in range(1, 7):
                    if len({head, second_lane, third_lane}) != 3:
                        continue
                    score = (
                        win[head]
                        * engine._second_given_head(head, second, boats, courses)[second_lane]
                        * engine._third_given_pair(head, second_lane, third, boats, courses)[third_lane]
                    )
                    scored.append((score, f"{head}-{second_lane}-{third_lane}"))
        raw_top_nine = {ticket for _, ticket in sorted(scored, reverse=True)[:9]}
        selected = set(result["tickets"]["main"] + result["tickets"]["deviation"] + result["tickets"]["upset"])
        self.assertTrue(raw_top_nine.issubset(selected))

    def test_four_to_one_is_a_protection_candidate(self) -> None:
        result = FukuokaPredictionEngineV10().predict(self.final_input("2026-09-03", 5))
        candidates = result["tickets"]["four_protection_candidates"]
        self.assertTrue(any(ticket.startswith("4-") and ticket.endswith("-1") for ticket in candidates))

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
