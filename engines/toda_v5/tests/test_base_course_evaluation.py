from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_prediction_engine_v5 import (
    COURSE_SCORE_DELTA_SCALE,
    TODA_COURSE_AVG_TOP3,
    TODA_COURSE_AVG_WIN,
    _course_performance,
)
from toda_utils_v5 import normalize_map


class BaseCourseEvaluationTests(unittest.TestCase):
    def test_small_sample_is_shrunk_with_eight_run_prior(self):
        result = _course_performance({"starts": 2, "win_rate": 100, "top3_rate": 100}, 6)
        self.assertAlmostEqual(result["confidence"], 2 / 10)
        self.assertAlmostEqual(
            result["shrunkWin"],
            .2 * 100 + .8 * TODA_COURSE_AVG_WIN[6],
        )

    def test_missing_course_data_uses_toda_average(self):
        result = _course_performance({}, 5)
        self.assertEqual(result["confidence"], 0)
        self.assertAlmostEqual(result["shrunkWin"], TODA_COURSE_AVG_WIN[5])
        self.assertAlmostEqual(result["shrunkTop3"], TODA_COURSE_AVG_TOP3[5])
        self.assertAlmostEqual(result["score"], 0)

    def test_win_and_top3_are_weighted_seventy_thirty(self):
        profile = {"starts": 8, "win_rate": 30, "top3_rate": 70}
        result = _course_performance(profile, 2)
        expected_delta = .70 * (result["shrunkWin"] - result["avgWin"])
        expected_delta += .30 * (result["shrunkTop3"] - result["avgTop3"])
        self.assertAlmostEqual(result["weightedDelta"], expected_delta)
        self.assertAlmostEqual(result["score"], expected_delta / COURSE_SCORE_DELTA_SCALE)

    def test_strength_label_does_not_change_score(self):
        common = {"starts": 12, "win_rate": 25, "top3_rate": 60}
        good = _course_performance({**common, "strength": "得意"}, 3)
        weak = _course_performance({**common, "strength": "苦手"}, 3)
        self.assertAlmostEqual(good["score"], weak["score"])

    def test_probability_normalization(self):
        normalized = normalize_map({str(lane): lane * 1.3 for lane in range(1, 7)})
        self.assertAlmostEqual(sum(normalized.values()), 100.0)


if __name__ == "__main__":
    unittest.main()
