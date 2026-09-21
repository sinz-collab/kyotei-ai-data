from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_scenario_engine_v5 import apply_scenario_mix, detect_scenarios, inside_weakness


class Toda20260918GuardTests(unittest.TestCase):
    def test_inside_weakness_escape_rate_guards(self):
        adverse = {"avg_st": .30, "top3_vs_course_avg": -30}
        common = {"lane": 1, "avg_st": .30, "local_st": .30, "local_win": 1.0}
        high = inside_weakness({**common, "boaters_escape_rate": 60}, adverse)
        middle = inside_weakness({**common, "boaters_escape_rate": 50}, adverse)
        self.assertLessEqual(high["weakStrength"], .25)
        self.assertLessEqual(middle["weakStrength"], .45)

    def test_scenario_mix_share_caps(self):
        base = {"1": 14, "2": 30, "3": 14, "4": 14, "5": 14, "6": 14}
        normal = [{"head": 2, "weight": 1.0, "attackEstablishment": .85, "kimariteMatchup": .59}]
        _, normal_audit = apply_scenario_mix(base, normal)
        self.assertAlmostEqual(normal_audit["maxShare"], .15)
        self.assertAlmostEqual(normal_audit["appliedShare"], .15)

        strong = [{"head": 2, "weight": 1.0, "attackEstablishment": .85, "kimariteMatchup": .60}]
        _, strong_audit = apply_scenario_mix(base, strong)
        self.assertAlmostEqual(strong_audit["maxShare"], .20)
        self.assertAlmostEqual(strong_audit["appliedShare"], .20)

    def test_all_attack_weights_stay_at_or_below_one(self):
        racers = [
            {
                "lane": lane,
                "avg_st": .15,
                "local_win": 5.0,
                "nat_win": 5.0,
                "boaters_escape_rate": 20 if lane == 1 else 0,
                "boaters_sashi_rate": 20 if lane == 2 else 0,
                "boaters_makuri_rate": 20 if lane in (3, 4) else 0,
                "boaters_makuri_sashi_rate": 10 if lane in (3, 4) else 0,
            }
            for lane in range(1, 7)
        ]
        profiles = {
            str(lane): {"avg_st": .15, "win_rate": 15, "top3_vs_course_avg": 10}
            for lane in range(1, 7)
        }
        scenarios, _ = detect_scenarios(
            racers,
            profiles,
            {str(lane): 5.0 for lane in range(1, 7)},
            {"wind_speed": 6, "wave_height": 6, "tide_phase": "低潮"},
        )
        attack_weights = [row["weight"] for row in scenarios if row["head"] != 1]
        self.assertTrue(attack_weights)
        self.assertLessEqual(max(attack_weights), 1.0)

    def test_base_live_consensus_guard_boundaries(self):
        base = {str(lane): 100 / 6 for lane in range(1, 7)}
        cases = (
            (.85, .60, .20),
            (.85, .5999, .30),
            (.70, .45, .30),
            (.70, .4499, .40),
            (.6999, .60, .40),
        )
        for establishment, matchup, expected in cases:
            scenario = [{"head": 2, "weight": 1.0, "attackEstablishment": establishment, "kimariteMatchup": matchup}]
            apply_scenario_mix(base, scenario, consensus_lane=1)
            self.assertAlmostEqual(scenario[0]["scenarioGuardSuppressionRate"], expected)
            self.assertTrue(scenario[0]["scenarioGuardSuppressed"])

    def test_top_flip_allowed_only_for_strong_scenario_with_two_point_margin(self):
        base = {"1": 40, "2": 38, "3": 8, "4": 6, "5": 5, "6": 3}
        scenario = [{"head": 2, "weight": 1.0, "attackEstablishment": .90, "kimariteMatchup": .65}]
        result, audit = apply_scenario_mix(base, scenario)
        self.assertTrue(audit["topFlipAttempted"])
        self.assertTrue(audit["topFlipAllowed"])
        self.assertEqual(audit["finalTop"], 2)
        self.assertGreaterEqual(result["2"] - result["1"], 2.0)

    def test_weak_scenario_cannot_flip_top_and_is_normalized(self):
        base = {"1": 40, "2": 39, "3": 8, "4": 6, "5": 4, "6": 3}
        scenario = [{"head": 2, "weight": 1.0, "attackEstablishment": .84, "kimariteMatchup": .59}]
        result, audit = apply_scenario_mix(base, scenario)
        self.assertTrue(audit["topFlipAttempted"])
        self.assertFalse(audit["topFlipAllowed"])
        self.assertEqual(audit["finalTop"], 1)
        self.assertLess(audit["topFlipScale"], 1.0)
        self.assertAlmostEqual(sum(result.values()), 100.0)

    def test_strong_scenario_below_two_point_margin_cannot_flip_top(self):
        base = {"1": 50, "2": 49.9, "3": .025, "4": .025, "5": .025, "6": .025}
        scenarios = [
            {"head": 2, "weight": .1, "attackEstablishment": .90, "kimariteMatchup": .65},
            *[
                {"head": lane, "weight": 1.0, "attackEstablishment": .10, "kimariteMatchup": .10}
                for lane in range(3, 7)
            ],
        ]
        result, audit = apply_scenario_mix(base, scenarios)
        self.assertTrue(audit["topFlipAttempted"])
        self.assertFalse(audit["topFlipAllowed"])
        self.assertLess(audit["topFlipMarginBeforeGuard"], 2.0)
        self.assertEqual(audit["finalTop"], 1)
        self.assertAlmostEqual(sum(result.values()), 100.0)


if __name__ == "__main__":
    unittest.main()
