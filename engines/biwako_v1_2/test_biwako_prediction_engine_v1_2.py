import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("biwako_v12", HERE / "biwako_prediction_engine_v1_2.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["biwako_v12"] = mod
spec.loader.exec_module(mod)

DB = HERE / "biwako_ai_master_fresh.sqlite"
if not DB.exists():
    DB = HERE.parents[1] / "data" / "venues" / "biwako" / "db" / "biwako_ai_master.sqlite"
CFG = HERE.parent / "biwako_v1_1" / "biwako_correction_v1_1.json"
if not CFG.exists():
    CFG = HERE / "biwako_correction_v1_1.json"
SAMPLE = HERE / "sample_biwako_race_input.json"
if not SAMPLE.exists():
    SAMPLE = HERE.parent / "biwako_v1_1" / "sample_biwako_race_input.json"


class TestBiwakoV12(unittest.TestCase):
    def setUp(self):
        self.eng = mod.BiwakoPredictionEngineV12(DB, CFG)
        self.race = json.loads(SAMPLE.read_text(encoding="utf-8"))

    def tearDown(self):
        self.eng.close()

    def test_probability_columns_normalize(self):
        out = self.eng.predict(copy.deepcopy(self.race), "preliminary")
        for key in ("win_prob", "second_prob", "third_prob"):
            self.assertAlmostEqual(sum(b[key] for b in out["boats"]), 1.0, places=5)

    def test_exactly_10_unique_tickets(self):
        out = self.eng.predict(copy.deepcopy(self.race), "preliminary")
        ts = out["tickets"]["main"] + out["tickets"]["deviation"] + out["tickets"]["upset"]
        self.assertEqual(len(ts), 10)
        self.assertEqual(len(set(ts)), 10)

    def test_odds_result_invariant(self):
        a = self.eng.predict(copy.deepcopy(self.race), "preliminary")
        b = copy.deepcopy(self.race)
        b["odds"] = {"1-2-3": 999.9}
        b["result"] = [6, 5, 4]
        c = self.eng.predict(b, "preliminary")
        self.assertEqual(
            [(x["win_prob"], x["second_prob"], x["third_prob"]) for x in a["boats"]],
            [(x["win_prob"], x["second_prob"], x["third_prob"]) for x in c["boats"]],
        )
        self.assertEqual(a["tickets"], c["tickets"])

    def test_head_slot_policy_6r_shape(self):
        probs = {
            1: {"win": .4185}, 2: {"win": .0922}, 3: {"win": .1743},
            4: {"win": .2032}, 5: {"win": .1021}, 6: {"win": .0097},
        }
        joint = []
        for h in probs:
            for i in range(8):
                joint.append({"lanes": (h, 1 if h != 1 else 2, 3), "score": .1/(i+1), "ticket": f"{h}-x-{i}"})
        slots = self.eng._head_slot_targets(probs, joint)
        self.assertEqual(slots[1], 4)
        self.assertEqual(slots[4], 3)
        self.assertEqual(slots[3], 1)
        self.assertEqual(slots[5], 1)
        self.assertEqual(slots[2], 1)

    def test_head_slot_policy_7r_shape(self):
        probs = {
            1: {"win": .558}, 2: {"win": .147}, 3: {"win": .085},
            4: {"win": .054}, 5: {"win": .043}, 6: {"win": .113},
        }
        joint = []
        for h in probs:
            for i in range(8):
                joint.append({"lanes": (h, 1 if h != 1 else 2, 3), "score": .1/(i+1), "ticket": f"{h}-x-{i}"})
        slots = self.eng._head_slot_targets(probs, joint)
        self.assertEqual(slots[1], 7)
        self.assertEqual(slots[2], 1)
        self.assertEqual(slots[6], 1)
        self.assertEqual(slots[3], 1)

    def test_head_slot_policy_8r_shape(self):
        probs = {
            1: {"win": .468}, 2: {"win": .208}, 3: {"win": .097},
            4: {"win": .056}, 5: {"win": .124}, 6: {"win": .047},
        }
        joint = []
        for h in probs:
            for i in range(8):
                joint.append({"lanes": (h, 1 if h != 1 else 2, 3), "score": .1/(i+1), "ticket": f"{h}-x-{i}"})
        slots = self.eng._head_slot_targets(probs, joint)
        self.assertEqual(slots[1], 5)
        self.assertEqual(slots[2], 3)
        self.assertEqual(slots[5], 1)
        self.assertEqual(slots[3], 1)


if __name__ == "__main__":
    unittest.main()
