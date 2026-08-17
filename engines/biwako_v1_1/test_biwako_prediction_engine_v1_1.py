import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("biwako_v11", HERE / "biwako_prediction_engine_v1_1.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["biwako_v11"] = mod
spec.loader.exec_module(mod)

DB = HERE / "biwako_ai_master_fresh.sqlite"
CFG = HERE / "biwako_correction_v1_1.json"
SAMPLE = HERE / "sample_biwako_race_input.json"


class TestBiwakoV11(unittest.TestCase):
    def setUp(self):
        self.eng = mod.BiwakoPredictionEngineV11(DB, CFG)
        self.race = json.loads(SAMPLE.read_text(encoding="utf-8"))
        self.race["event_day"] = 3
        self.race["final_day_flag"] = False
        for i, b in enumerate(self.race["boats"], 1):
            b["setsukan_runs"] = [
                {"course": max(1, i-1), "finish": 4, "st": 0.18},
                {"course": i, "finish": 2 if i <= 3 else 4, "st": 0.13 + i * 0.005},
            ]

    def tearDown(self):
        self.eng.close()

    def add_final(self, race):
        ex = [6.74, 6.72, 6.68, 6.83, 6.72, 6.81]
        st = [0.13, 0.09, 0.06, 0.06, 0.04, 0.03]
        lap = [37.40, 37.49, 37.31, 37.88, 37.54, 38.71]
        turn = [5.67, 5.61, 5.91, 6.17, 6.00, 6.42]
        straight = [7.87, 8.05, 7.93, 7.83, 7.73, 7.68]
        sums = [44.14, 44.21, 43.99, 44.71, 44.26, 45.52]
        for i, b in enumerate(race["boats"]):
            b.update(exhibition_time=ex[i], exhibition_st=st[i], original_lap=lap[i],
                     original_turn=turn[i], original_straight=straight[i], original_sum=sums[i])
        return race

    def test_preliminary_probability_columns_sum_to_one(self):
        out = self.eng.predict(self.race, "preliminary")
        for key in ("win_prob", "second_prob", "third_prob"):
            self.assertAlmostEqual(sum(x[key] for x in out["boats"]), 1.0, places=5)

    def test_day3_blend_is_60_40(self):
        out = self.eng.predict(self.race, "preliminary")
        self.assertEqual(out["rules"]["event_day_blend"], {"motor_exhibition": 0.60, "setsukan": 0.40})

    def test_preliminary_ignores_exhibition_and_original(self):
        a = self.eng.predict(self.race, "preliminary")
        b = self.eng.predict(self.add_final(copy.deepcopy(self.race)), "preliminary")
        av = [(x["win_prob"], x["second_prob"], x["third_prob"]) for x in a["boats"]]
        bv = [(x["win_prob"], x["second_prob"], x["third_prob"]) for x in b["boats"]]
        self.assertEqual(av, bv)

    def test_final_uses_exhibition_original_and_slit(self):
        pre = self.eng.predict(self.race, "preliminary")
        final = self.eng.predict(self.add_final(copy.deepcopy(self.race)), "final")
        self.assertIsNotNone(final["live_adjustment"])
        self.assertIn("slit_geometry", final["live_adjustment"])
        self.assertIn("original_exhibition_signal_by_lane", final["live_adjustment"])
        self.assertNotEqual([x["win_prob"] for x in pre["boats"]], [x["win_prob"] for x in final["boats"]])

    def test_odds_and_result_are_not_used(self):
        race2 = copy.deepcopy(self.race)
        race2["odds"] = {"1-2-3": 1.0}
        race2["result"] = {"trifecta": "6-5-4"}
        for b in race2["boats"]:
            b["odds"] = 9999
            b["result"] = 1
        a = self.eng.predict(self.race, "preliminary")
        b = self.eng.predict(race2, "preliminary")
        self.assertEqual([(x["win_prob"], x["second_prob"], x["third_prob"]) for x in a["boats"]],
                         [(x["win_prob"], x["second_prob"], x["third_prob"]) for x in b["boats"]])
        self.assertFalse(b["rules"]["odds_used"])
        self.assertFalse(b["rules"]["result_used"])

    def test_ticket_count_and_uniqueness(self):
        out = self.eng.predict(self.add_final(copy.deepcopy(self.race)), "final")
        tickets = out["tickets"]["main"] + out["tickets"]["deviation"] + out["tickets"]["upset"]
        self.assertEqual(len(tickets), 10)
        self.assertEqual(len(set(tickets)), 10)

    def test_conditional_ticket_layer_can_keep_3_2_5_over_3_2_1(self):
        # Structural regression: when C3 wins, C2 is second and current-layout conditional/order
        # plus external-link evidence favors C5 for third, the ticket layer must preserve 3-2-5.
        probs = {
            1:{"win":.31,"second":.20,"third":.17}, 2:{"win":.28,"second":.27,"third":.22},
            3:{"win":.22,"second":.24,"third":.22}, 4:{"win":.10,"second":.13,"third":.17},
            5:{"win":.07,"second":.13,"third":.18}, 6:{"win":.02,"second":.03,"third":.04},
        }
        states = [mod.BoatState(i,i,str(1000+i),str(i),{}, {}) for i in range(1,7)]
        self.eng._conditional_order_db = lambda first, second: ({5:(.40,150),1:(.18,150),4:(.20,150),6:(.22,150)} if (first,second)==(3,2) else {})
        live = {"slit_geometry":{"outer_link":{5:1.0}}}
        joint = self.eng._conditional_ticket_probs(probs, states, [], live)
        score = {r["ticket"]: r["score"] for r in joint}
        self.assertGreater(score["3-2-5"], score["3-2-1"])


if __name__ == "__main__":
    unittest.main()
