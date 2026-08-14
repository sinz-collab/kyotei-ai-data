import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_scenario_engine_v5 import detect_scenarios


class TodaV6OuterChainTest(unittest.TestCase):
    def test_outer_head_not_created_from_lane1_weakness_alone(self):
        racers = []
        profiles = {}
        scores = {str(i): 0.0 for i in range(1, 7)}
        for lane in range(1, 7):
            racer = {
                "lane": lane,
                "nat_win": 4.5,
                "local_win": 4.5,
                "avg_st": .18,
                "local_st": .18,
                "boaters_escape_rate": 20 if lane == 1 else 0,
                "boaters_sashare_rate": 35 if lane == 1 else 0,
                "boaters_makurare_rate": 30 if lane == 1 else 0,
                "boaters_makurare_zashi_rate": 25 if lane == 1 else 0,
                "boaters_sashi_rate": 0,
                "boaters_makuri_rate": 8 if lane in (5, 6) else 0,
                "boaters_makuri_sashi_rate": 8 if lane in (5, 6) else 0,
            }
            racers.append(racer)
            profiles[str(lane)] = {"avg_st": .18, "top3_vs_course_avg": 0, "win_rate": 15 if lane in (5, 6) else 0}
        scores["1"] = -2.0
        scores["5"] = 1.0
        scores["6"] = .9
        scenarios, _ = detect_scenarios(racers, profiles, scores, {})
        heads = {int(x["head"]) for x in scenarios}
        self.assertNotIn(5, heads)
        self.assertNotIn(6, heads)


if __name__ == "__main__":
    unittest.main()
