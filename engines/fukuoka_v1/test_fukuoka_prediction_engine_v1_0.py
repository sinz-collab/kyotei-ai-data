import unittest
from unittest.mock import patch

import fukuoka_prediction_engine_v1_0 as engine_module
from fukuoka_prediction_engine_v1_0 import FukuokaPredictionEngineV10


def boat(lane, course=None, **kw):
    c = course or lane
    d = {
        "lane": lane, "actual_course": c,
        "national_win_rate": 5.0, "local_win_rate": 5.0, "local_avg_st": 0.17,
        "course_escape_rate": 53.35 if c == 1 else 0,
        "course_sashi_rate": 0, "course_makuri_rate": 0, "course_makuri_sashi_rate": 0,
        "motor_grade": "C", "motor_trend": "flat", "setsukan_runs": [],
        "exhibition_time": 6.95 + lane * 0.01, "exhibition_st": 0.10 + lane * 0.01,
        "original_lap": 37.5 + lane * 0.05, "original_turn": 5.5 + lane * 0.03,
        "original_straight": 7.6 + lane * 0.01,
    }
    d.update(kw)
    return d


class TestFukuokaV10(unittest.TestCase):
    def setUp(self): self.e = FukuokaPredictionEngineV10()

    def unadjusted_race(self):
        boats = [boat(i) for i in range(1, 7)]
        for row in boats:
            for key in (
                "exhibition_time", "exhibition_st", "original_lap",
                "original_turn", "original_straight",
            ):
                row.pop(key, None)
        return {"event_day": 1, "wind_speed": 0, "wave_height": 4, "boats": boats}

    def test_contract_and_10_tickets(self):
        r = self.e.predict({"event_day": 2, "wind_direction": "NE", "wind_speed": 4, "wave_height": 4, "tide_phase": "falling", "boats": [boat(i) for i in range(1, 7)]})
        self.assertEqual(r["engine"], "fukuoka_engine_v1.0")
        self.assertEqual(len(r["tickets"]["main"]), 6)
        self.assertEqual(len(r["tickets"]["deviation"]), 2)
        self.assertEqual(len(r["tickets"]["upset"]), 2)
        self.assertFalse(r["diagnostics"]["odds_used"])
        self.assertFalse(r["diagnostics"]["result_used"])

    def test_probabilities_normalize(self):
        r = self.e.predict({"event_day": 1, "boats": [boat(i) for i in range(1, 7)]})
        for key in ("win_prob", "second_prob", "third_prob"):
            self.assertAlmostEqual(sum(b[key] for b in r["boats"]), 1.0, places=5)
        self.assertTrue(all(b["win_prob"] >= 0.0 for b in r["boats"]))

    def test_unadjusted_win_stays_at_fukuoka_base_probability(self):
        with (
            patch.object(engine_module, "wind_speed_delta", return_value=0.0),
            patch.object(engine_module, "wave_delta", return_value=0.0),
        ):
            r = self.e.predict(self.unadjusted_race(), debug=True)
        lane1 = next(row for row in r["boats"] if row["actual_course"] == 1)
        self.assertAlmostEqual(lane1["win_prob"] * 100.0, 53.35, places=2)
        self.assertEqual(r["diagnostics"]["win_normalization"], "linear_percent_points")

    def test_win_generation_does_not_use_softmax(self):
        uniform = {lane: 1.0 / 6.0 for lane in range(1, 7)}
        with (
            patch.object(engine_module, "softmax", return_value=uniform),
            patch.object(engine_module, "wind_speed_delta", return_value=0.0),
            patch.object(engine_module, "wave_delta", return_value=0.0),
        ):
            r = self.e.predict(self.unadjusted_race())
        lane1 = next(row for row in r["boats"] if row["actual_course"] == 1)
        self.assertAlmostEqual(lane1["win_prob"] * 100.0, 53.35, places=2)

    def test_escape_minus_nine_is_applied_as_percentage_points(self):
        baseline = self.unadjusted_race()
        weak = self.unadjusted_race()
        for race in (baseline, weak):
            race["boats"][2].update(course_makuri_rate=10.0, course_makuri_sashi_rate=5.0)
        baseline["boats"][0]["course_escape_rate"] = 50.0
        weak["boats"][0]["course_escape_rate"] = 5.0
        baseline_result = self.e.predict(baseline, debug=True)
        weak_result = self.e.predict(weak, debug=True)
        baseline_audit = next(row for row in baseline_result["diagnostics"]["win_audit"] if row["actual_course"] == 1)
        weak_audit = next(row for row in weak_result["diagnostics"]["win_audit"] if row["actual_course"] == 1)
        self.assertAlmostEqual(weak_audit["course_delta"], -9.0, places=6)
        self.assertAlmostEqual(baseline_audit["raw_win"] - weak_audit["raw_win"], 9.0, places=6)

    def test_course_four_plus_six_is_linear_percentage_points(self):
        race = self.unadjusted_race()
        race["boats"][3].update(course_makuri_rate=50.0, course_makuri_sashi_rate=25.0)
        r = self.e.predict(race, debug=True)
        audit = next(row for row in r["diagnostics"]["win_audit"] if row["actual_course"] == 4)
        self.assertAlmostEqual(audit["course_delta"], 6.0, places=6)
        self.assertAlmostEqual(audit["raw_win"], 8.70 + 6.0, places=6)
        self.assertLess(next(row["win_prob"] for row in r["boats"] if row["actual_course"] == 4), 0.20)

    def test_win_delta_limits_are_pre_ten_and_live_thirteen(self):
        pre = self.e.predict(self.unadjusted_race(), debug=True)
        live = self.e.predict({"event_day": 2, "boats": [boat(i) for i in range(1, 7)]}, debug=True)
        self.assertEqual(pre["diagnostics"]["win_delta_limit"], 10.0)
        self.assertEqual(live["diagnostics"]["win_delta_limit"], 13.0)
        self.assertTrue(all(abs(row["total_delta"]) <= 10.0 for row in pre["diagnostics"]["win_audit"]))
        self.assertTrue(all(abs(row["total_delta"]) <= 13.0 for row in live["diagnostics"]["win_audit"]))

    def test_actual_course_rebuild(self):
        boats = [boat(1,1), boat(2,4), boat(3,3), boat(4,5), boat(5,2,course_sashi_rate=35,exhibition_st=0.01,exhibition_time=6.88), boat(6,6)]
        r = self.e.predict({"event_day": 1, "boats": boats})
        self.assertEqual(next(b for b in r["boats"] if b["lane"] == 5)["actual_course"], 2)

    def test_local_st_exemption(self):
        boats = [boat(i) for i in range(1, 7)]
        boats[0].update(local_win_rate=6.7,national_win_rate=5.8,local_avg_st=0.14,course_escape_rate=72.0,exhibition_st=0.30)
        r = self.e.predict({"event_day": 2, "boats": boats})
        self.assertGreater(next(b["win_prob"] for b in r["boats"] if b["lane"] == 1), 0.30)

    def test_lane2_floor_is_soft(self):
        boats = [boat(i) for i in range(1, 7)]
        boats[0]["course_escape_rate"] = 45
        boats[1].update(course_sashi_rate=30, exhibition_st=0.01, exhibition_time=6.88)
        r = self.e.predict({"event_day": 2, "boats": boats})
        p2 = next(b["win_prob"] for b in r["boats"] if b["lane"] == 2)
        self.assertGreater(p2, 0.14)
        self.assertLess(p2, 0.45)


if __name__ == "__main__": unittest.main()
