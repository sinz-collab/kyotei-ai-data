import inspect
from pathlib import Path
import sys
import unittest


ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_scenario_engine_v5 import current_form_inside_resistance, detect_scenarios
from toda_ticket_engine_v5 import build_tickets, build_upset_tickets


class TodaCurrentFormAndFixedTicketTests(unittest.TestCase):
    def _conditionals(self):
        second = {}
        third = {}
        for head in range(1, 7):
            second[str(head)] = {
                str(lane): (0.0 if lane == head else 32 - lane * 2)
                for lane in range(1, 7)
            }
            third[str(head)] = {
                str(lane): (0.0 if lane == head else 28 - lane)
                for lane in range(1, 7)
            }
        return second, third

    def _scenarios(self):
        return [
            {"id": "CURRENT_FORM_INSIDE_RESISTANCE", "head": 1, "weight": .8, "links": [2, 3, 4, 5, 6]},
            {"id": "TWO_SASHI", "head": 2, "weight": .9, "attackEstablishment": .9, "links": [1, 3, 4, 5, 6]},
            {"id": "FOUR_KADO", "head": 4, "weight": .85, "attackEstablishment": .85, "links": [5, 6, 1, 3, 2]},
            {"id": "OUTER_5", "head": 5, "weight": .7, "attackChain": True, "links": [1, 3, 4, 2, 6]},
        ]

    def test_sab_all_use_fixed_ten_with_exact_roles_and_no_duplicates(self):
        win = {"1": 42, "2": 21, "3": 14, "4": 11, "5": 7, "6": 5}
        second, third = self._conditionals()
        for sab in ("S", "A", "B"):
            tickets = build_tickets(win, second, third, self._scenarios(), sab)
            roles = [row["role"] for row in tickets]
            self.assertEqual(len(tickets), 10)
            self.assertEqual(len({row["combo"] for row in tickets}), 10)
            self.assertEqual(roles.count("本線"), 6)
            self.assertEqual(roles.count("2着ズレ"), 1)
            self.assertEqual(roles.count("3着ズレ"), 1)
            self.assertEqual(roles.count("シナリオ穴"), 2)
            self.assertTrue(all(row["odds"] == "-" for row in tickets))

    def test_good_inside_requires_three_runs_and_stability(self):
        good = {
            "boaters_escape_rate": 50,
            "motor_recent": {"trend": "up"},
            "season_runs": [
                {"finish": "2着", "st": ".14"},
                {"finish": "1着", "st": ".17"},
                {"finish": "2着", "st": ".09"},
            ],
        }
        self.assertTrue(current_form_inside_resistance(good)["applied"])
        self.assertFalse(current_form_inside_resistance({**good, "season_runs": good["season_runs"][:2]})["applied"])
        poor = {**good, "season_runs": [
            {"finish": "6着", "st": ".17"},
            {"finish": "6着", "st": ".14"},
            {"finish": "4着", "st": ".19"},
        ]}
        self.assertFalse(current_form_inside_resistance(poor)["applied"])

    def test_inside_resistance_is_a_relative_scenario_not_a_probability_floor(self):
        racers = [
            {"lane": lane, "avg_st": .17, "local_win": 5, "nat_win": 5}
            for lane in range(1, 7)
        ]
        racers[0].update({
            "boaters_escape_rate": 25,
            "season_runs": [
                {"finish": "2着", "st": ".14"},
                {"finish": "1着", "st": ".17"},
                {"finish": "2着", "st": ".09"},
            ],
            "motor_recent": {"trend": "up"},
        })
        profiles = {str(lane): {"avg_st": .17, "win_rate": 0, "top3_vs_course_avg": 0} for lane in range(1, 7)}
        scenarios, _ = detect_scenarios(racers, profiles, {str(lane): 0 for lane in range(1, 7)}, {})
        resistance = [row for row in scenarios if row["id"] == "CURRENT_FORM_INSIDE_RESISTANCE"]
        self.assertEqual(len(resistance), 1)
        self.assertGreater(resistance[0]["weight"], .48)
        self.assertNotIn("minimumP1", resistance[0])

    def test_lane_one_can_be_scenario_hole_and_ai_upset_never_overlaps(self):
        win = {"1": 18, "2": 12, "3": 10, "4": 45, "5": 9, "6": 6}
        second, third = self._conditionals()
        normal = build_tickets(win, second, third, self._scenarios(), "A")
        holes = [row for row in normal if row["role"] == "シナリオ穴"]
        self.assertTrue(any(row["combo"].startswith("1-") for row in holes))

        normal_combos = {row["combo"] for row in normal}
        normal_heads = {int(combo.split("-")[0]) for combo in normal_combos}
        upset = build_upset_tickets(
            win, second, third, self._scenarios(),
            exclude_combos=normal_combos,
            exclude_heads=normal_heads,
            role="AI荒れ",
            scenario_only=True,
        )
        self.assertFalse(normal_combos & {row["combo"] for row in upset})
        self.assertTrue(all(int(row["combo"].split("-")[0]) not in normal_heads for row in upset))

    def test_ticket_generation_has_no_odds_or_date_race_branch_inputs(self):
        source = inspect.getsource(sys.modules[build_tickets.__module__])
        self.assertNotIn("2026-", source)
        self.assertNotIn("race_no", source)
        self.assertNotIn("odds.get", source)
        self.assertNotIn("odds_used", source)


if __name__ == "__main__":
    unittest.main()
