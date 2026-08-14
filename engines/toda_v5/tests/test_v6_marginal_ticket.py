import sys
import unittest
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from toda_ticket_engine_v5 import marginal_second, marginal_third, build_tickets


class TodaV6MarginalTicketTest(unittest.TestCase):
    def _conditionals(self):
        second = {}
        third = {}
        for h in range(1, 7):
            allowed = [i for i in range(1, 7) if i != h]
            sec = {str(i): (0.0 if i == h else 20.0) for i in range(1, 7)}
            thr = {str(i): (0.0 if i == h else 20.0) for i in range(1, 7)}
            second[str(h)] = sec
            third[str(h)] = thr
        return second, third

    def test_public_marginals_sum_to_100(self):
        win = {"1": 43.0, "2": 20.0, "3": 17.0, "4": 8.0, "5": 7.0, "6": 5.0}
        second, third = self._conditionals()
        m2 = marginal_second(win, second)
        m3 = marginal_third(win, second, third)
        self.assertLessEqual(abs(sum(m2.values()) - 100.0), 0.5)
        self.assertLessEqual(abs(sum(m3.values()) - 100.0), 0.5)
        self.assertEqual(set(m2), {"1", "2", "3", "4", "5", "6"})
        self.assertEqual(set(m3), {"1", "2", "3", "4", "5", "6"})

    def test_clear_axis_keeps_second_place_drift_candidate(self):
        win = {"1": 43.0, "2": 20.0, "3": 17.0, "4": 8.0, "5": 7.0, "6": 5.0}
        second, third = self._conditionals()
        second["1"] = {"1": 0.0, "2": 30.0, "3": 27.0, "4": 18.0, "5": 16.0, "6": 9.0}
        third["1"] = {"1": 0.0, "2": 25.0, "3": 24.0, "4": 19.0, "5": 18.0, "6": 14.0}
        scenarios = [{"head": 1, "links": [2, 3, 4, 5, 6]}, {"head": 2, "links": [1, 3, 4, 5, 6]}]
        tickets = build_tickets(win, second, third, scenarios, "A")
        self.assertTrue(any(x["combo"].startswith("1-4-") or x["combo"].startswith("1-5-") for x in tickets))
        self.assertTrue(all(x["odds"] == "-" for x in tickets))


if __name__ == "__main__":
    unittest.main()
