import unittest
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
