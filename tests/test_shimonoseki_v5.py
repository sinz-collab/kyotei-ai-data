from __future__ import annotations

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = ROOT
FIX_CANDIDATE = ROOT / "tests" / "fixtures" / "shimonoseki_v5"
FIX = FIX_CANDIDATE if FIX_CANDIDATE.is_dir() else ROOT / "tests" / "fixtures"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from engines.shimonoseki_v5.shimonoseki_engine_v5 import ShimonosekiSiteEngineV5, ENGINE_ID

MASTER = PKG_ROOT / "engines" / "shimonoseki_v5" / "master"


class ShimonosekiV5RegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = ShimonosekiSiteEngineV5(MASTER)
        cls.source = json.loads((FIX / "daily_20260819.json").read_text(encoding="utf-8"))

    def live_docs(self, race_no: int):
        root = FIX / f"live_r{race_no:02d}"
        return {name: json.loads((root / f"{name}.json").read_text(encoding="utf-8")) for name in ("direct","exhibition","original_exhibition")}

    def test_preliminary_all_12_complete(self):
        payload = self.engine.apply_preliminary_daily(deepcopy(self.source))
        ok, reason = self.engine.validate_payload(payload)
        self.assertTrue(ok, reason)
        self.assertEqual(payload["engine"], ENGINE_ID)
        self.assertEqual(len(payload["preds"]), 12)
        for race in payload["races"]:
            self.assertIn("predictionPre", race)
            self.assertEqual(len(payload["preds"][str(race["race"])]["tickets"]), 10)

    def test_regression_1r_2r_10r(self):
        payload = self.engine.apply_preliminary_daily(deepcopy(self.source))
        refs = {
            1: {"win":{"1":58.89,"2":7.20,"3":10.77,"4":18.93,"5":3.09,"6":1.12}, "main":"1-4-2", "upset":"3-4-5"},
            2: {"win":{"1":33.30,"2":14.24,"3":24.96,"4":11.84,"5":9.32,"6":6.34}, "main":"1-3-4", "upset":"5-6-4"},
            10:{"win":{"1":49.84,"2":22.23,"3":13.21,"4":10.50,"5":2.67,"6":1.54}, "main":"1-2-4", "upset":"4-5-3"},
        }
        for rn, ref in refs.items():
            pre = deepcopy(payload["preds"][str(rn)])
            docs = self.live_docs(rn)
            self.engine.apply_final_race(payload, rn, docs["direct"], docs["exhibition"], docs["original_exhibition"])
            race = next(r for r in payload["races"] if r["race"] == rn)
            pred = race["predictionFinal"]
            site_pred = payload["preds"][str(rn)]
            # Site implementation must stay within the same practical probability band.
            for lane, expected in ref["win"].items():
                self.assertLessEqual(abs(float(pred["win"][lane]) - expected), 0.85, (rn, lane, pred["win"][lane], expected))
            self.assertEqual(pred["tickets"]["main"][0]["combo"], ref["main"])
            self.assertEqual(pred["tickets"]["upset"][0]["combo"], ref["upset"])
            self.assertFalse(pred["debug"]["result_used"])
            self.assertFalse(pred["debug"]["odds_used"])
            self.assertEqual(
                site_pred["predictionPre"],
                {key: pre[key] for key in ("win", "second", "third")},
            )
            self.assertEqual(site_pred["probabilityReviewStatus"], "reviewed")
            self.assertTrue(site_pred["probabilityFlow"]["reviewed"])

    def test_10r_attack_link_is_conditional(self):
        payload = self.engine.apply_preliminary_daily(deepcopy(self.source))
        docs = self.live_docs(10)
        self.engine.apply_final_race(payload, 10, docs["direct"], docs["exhibition"], docs["original_exhibition"])
        race = next(r for r in payload["races"] if r["race"] == 10)
        pred = race["predictionFinal"]
        self.assertEqual(pred["debug"]["scenario_transfer"]["head"], 4)
        self.assertEqual(pred["tickets"]["upset"][0]["combo"], "4-5-3")
        self.assertEqual(pred["tickets"]["upset"][1]["combo"], "4-2-3")


if __name__ == "__main__":
    unittest.main()
