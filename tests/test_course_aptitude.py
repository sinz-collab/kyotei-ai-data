import math
import json
import unittest
from pathlib import Path

from automation.course_aptitude import (
    CourseAptitudeEvaluator,
    CourseRow,
    class_period_code,
    parse_fan_classes,
)
from datetime import date


def row(reg_no, rate, starts=30, course=2):
    return CourseRow(
        reg_no=str(reg_no),
        racer_name=f"選手{reg_no}",
        course=course,
        starts=starts,
        win_rate=rate,
        top2_rate=min(rate + 0.2, 1.0),
        top3_rate=min(rate + 0.4, 1.0),
        average_st=0.15,
        kimarite={"逃げ": 0, "差し": 1, "まくり": 2, "まくり差し": 3, "抜き": 0, "恵まれ": 0},
    )


class CourseAptitudeTest(unittest.TestCase):
    def setUp(self):
        self.subject = row(4000, 0.70)
        peers = [row(4100 + index, 0.10 + index * 0.03) for index in range(12)]
        classes = {item.reg_no: "A1" for item in [self.subject, *peers]}
        self.evaluator = CourseAptitudeEvaluator(
            [self.subject, *peers],
            classes,
            {"rolling_as_of": "2026-10-30", "rolling_window_start": "2026-05-01"},
        )

    def test_fixed_formula_excludes_subject_and_uses_rates_zero_to_one(self):
        result = self.evaluator.evaluate(
            {"player_id": 4000, "name": "選手4000", "class": "A1", "actual_course": 2}
        )
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["aptitude_label"], "得意")
        self.assertEqual(result["comparison_size"], 12)
        self.assertEqual(result["win_rate"], 0.70)
        series = [
            [0.10 + index * 0.03 for index in range(12)],
            [0.30 + index * 0.03 for index in range(12)],
            [0.50 + index * 0.03 for index in range(12)],
        ]
        subject_rates = (0.70, 0.90, 1.0)
        z_scores = []
        for subject_rate, values in zip(subject_rates, series):
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            z_scores.append((subject_rate - mean) / math.sqrt(variance))
        weighted_z = 0.3 * z_scores[0] + 0.3 * z_scores[1] + 0.4 * z_scores[2]
        expected = 50 + 10 * weighted_z * 30 / 50
        self.assertAlmostEqual(result["aptitude_score_raw"], expected)

    def test_unavailable_conditions(self):
        low_starts = row(4001, 0.5, starts=9)
        evaluator = CourseAptitudeEvaluator(
            [low_starts, *self.evaluator.rows[1:]],
            {item.reg_no: "A1" for item in [low_starts, *self.evaluator.rows[1:]]},
        )
        result = evaluator.evaluate({"player_id": 4001, "actual_course": 2})
        self.assertEqual(result["aptitude_label"], "判定不可")
        self.assertEqual(result["reason"], "starts_under_10")

        too_few = CourseAptitudeEvaluator(
            [self.subject, *self.evaluator.rows[1:6]],
            {item.reg_no: "A1" for item in [self.subject, *self.evaluator.rows[1:6]]},
        ).evaluate({"player_id": 4000, "actual_course": 2})
        self.assertEqual(too_few["reason"], "comparison_group_under_10")

        equal_peers = [row(4200 + index, 0.2) for index in range(10)]
        zero_sd = CourseAptitudeEvaluator(
            [self.subject, *equal_peers],
            {item.reg_no: "A1" for item in [self.subject, *equal_peers]},
        ).evaluate({"player_id": 4000, "actual_course": 2})
        self.assertEqual(zero_sd["reason"], "standard_deviation_zero")

    def test_exhibition_course_and_both_payload_collections(self):
        racer = {"player_id": 4000, "lane": 1, "actual_course": 2, "name": "選手4000"}
        entry = dict(racer)
        payload = {"races": [{"racers": [racer], "entries": [entry]}]}
        counts = self.evaluator.enrich_payload(payload)
        self.assertEqual(counts, {"available": 1, "unavailable": 0})
        self.assertEqual(racer["recent_course_6m"]["course"], 2)
        self.assertEqual(entry["recent_course_6m"]["course"], 2)
        self.assertEqual(set(racer["recent_course_6m_by_course"]), {"1", "2", "3", "4", "5", "6"})
        self.assertNotIn("recent_course_6m_by_course", entry)

    def test_official_fan_layout_and_period(self):
        line = bytearray(b" " * 416)
        line[0:4] = b"4321"
        line[39:41] = b"A2"
        self.assertEqual(parse_fan_classes(bytes(line) + b"\r\n"), {"4321": "A2"})
        self.assertEqual(class_period_code(date(2026, 6, 30)), "2510")
        self.assertEqual(class_period_code(date(2026, 7, 1)), "2604")

    def test_all_configured_venues_use_the_same_enricher(self):
        config = json.loads(
            (Path(__file__).parents[1] / "automation" / "venues.json").read_text(encoding="utf-8")
        )
        venues = [item["slug"] for item in config["venues"]]
        self.assertEqual(len(venues), 11)
        for venue in venues:
            racer = {"player_id": 4000, "lane": 2, "actual_course": 2, "name": "選手4000"}
            payload = {"venueId": venue, "races": [{"racers": [racer]}]}
            counts = self.evaluator.enrich_payload(payload)
            self.assertEqual(counts["available"], 1, venue)
            self.assertEqual(racer["recent_course_6m"]["source"], "official_race_results")


if __name__ == "__main__":
    unittest.main()
