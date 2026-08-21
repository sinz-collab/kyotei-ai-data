from __future__ import annotations

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path

from engines.shimonoseki_v6.shimonoseki_engine_v6 import (
    ENGINE_ID,
    ENGINE_VERSION,
    ShimonosekiSiteEngineV6,
)
from engines.shimonoseki_v6.shimonoseki_v6_core import ShimonosekiV6Core

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "engines" / "shimonoseki_v6" / "master"
CASE_PATH = ROOT / "tests" / "fixtures" / "shimonoseki_v6" / "r5_case.py"
SPEC = importlib.util.spec_from_file_location("shimonoseki_v6_r5_case", CASE_PATH)
assert SPEC and SPEC.loader
CASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CASE)
RACE, EX, OG = CASE.RACE, CASE.EX, CASE.OG

DIRECT = {"data": {"wind_speed": 3, "wave_height": 3, "wind_direction": 7}}
TIDE = [
    {"time": "14:00", "type": "満潮", "level": 260},
    {"time": "20:00", "type": "干潮", "level": 80},
]


class ShimonosekiV6CoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = ShimonosekiV6Core(MASTER)

    def predict(self, tide_events=None):
        return self.engine.predict_final(RACE, EX, OG, direct=DIRECT, tide_events=tide_events or [])

    def test_20260820_r5_actual_course_remap_and_probability_regression(self):
        prediction = self.predict()
        self.assertEqual(
            prediction["debug"]["actual_course"],
            {1: 4, 2: 1, 3: 5, 4: 6, 5: 3, 6: 2},
        )
        expected = {"1": 16.81, "2": 47.11, "3": 12.18, "4": 4.15, "5": 3.60, "6": 16.15}
        for lane, value in expected.items():
            self.assertAlmostEqual(prediction["win"][lane], value, delta=0.05)
        self.assertEqual(prediction["debug"]["course_remap"][1]["actual_course"], 4)
        self.assertEqual(prediction["debug"]["course_remap"][2]["actual_course"], 1)

    def test_motor_recent_compound_attack_and_zero_attack_guard(self):
        prediction = self.predict()
        self.assertEqual(prediction["debug"]["motor"][1]["trend"], "up")
        self.assertEqual(prediction["debug"]["compound_attack"][3]["count"], 5)
        self.assertGreater(prediction["debug"]["compound_attack"][3]["attack_rate"], 0)
        self.assertEqual(tuple(prediction["debug"]["compound_attack"][1]["delta"]), (0, 0, 0))
        self.assertEqual(prediction["debug"]["compound_attack"][1]["attack_rate"], 0)

    def test_precise_tide_applied_once_and_sab_final_guard(self):
        prediction = self.predict(TIDE)
        tide = prediction["debug"]["water"]["tide"]
        self.assertIsNotNone(tide)
        self.assertEqual(tide["direction"], "falling")
        self.assertEqual(prediction["sab"], "A")
        self.assertTrue(prediction["debug"]["sab_guard"])
        self.assertEqual(prediction["debug"]["grade_cap"], "A")
        self.assertEqual(prediction["debug"]["changed_courses"], [1, 2, 3, 4, 5, 6])
        self.assertEqual(prediction["debug"]["entry_shift_size"], 14)
        self.assertEqual(prediction["debug"]["multi_head_count"], 3)
        self.assertTrue(prediction["debug"]["scenario_upset"])

    def test_probability_sums_audit_flags_and_tickets(self):
        prediction = self.predict()
        for key in ("win", "second", "third"):
            self.assertAlmostEqual(sum(prediction[key].values()), 100.0, delta=0.05)
        self.assertFalse(prediction["debug"]["result_used"])
        self.assertFalse(prediction["debug"]["odds_used"])
        tickets = prediction["tickets"]
        self.assertEqual([len(tickets[key]) for key in ("main", "deviation", "upset")], [6, 2, 2])
        combos = [item["combo"] for key in ("main", "deviation", "upset") for item in tickets[key]]
        self.assertEqual(len(combos), 10)
        self.assertEqual(len(set(combos)), 10)

    def test_result_and_odds_injection_cannot_change_prediction(self):
        clean = self.predict()
        race = deepcopy(RACE)
        exhibition = deepcopy(EX)
        original = deepcopy(OG)
        direct = deepcopy(DIRECT)
        race["result"] = {"trifecta": "6-5-4"}
        race["odds"] = {"6-5-4": 999999}
        direct["result"] = {"winner": 6}
        exhibition["odds"] = {"6-5-4": 1}
        original["result"] = [6, 5, 4]
        tainted = self.engine.predict_final(race, exhibition, original, direct=direct, tide_events=[])
        for key in ("win", "second", "third", "sab", "tickets"):
            self.assertEqual(tainted[key], clean[key])


class ShimonosekiV6EnvelopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = ShimonosekiSiteEngineV6(MASTER)

    @staticmethod
    def payload() -> dict:
        races = []
        for race_no in range(1, 13):
            race = deepcopy(RACE)
            race["race"] = race_no
            races.append(race)
        return {"venueId": "shimonoseki", "date": "2026-08-20", "races": races, "tide": {"events": []}}

    def test_preliminary_v6_12r_gate_and_legacy_compatibility(self):
        payload = self.engine.apply_preliminary_daily(self.payload())
        ok, reason = self.engine.validate_payload(payload, require_all=True)
        self.assertTrue(ok, reason)
        self.assertEqual(payload["engine"], ENGINE_ID)
        self.assertEqual(payload["engineVersion"], ENGINE_VERSION)
        self.assertEqual(set(payload["preds"]), {str(x) for x in range(1, 13)})
        for race in payload["races"]:
            self.assertIn("predictionPre", race)
            self.assertEqual(race["predictionPre"]["engine"], ENGINE_ID)
            legacy = payload["preds"][str(race["race"])]
            for key in ("win", "second", "third", "sab", "ai", "balance", "aiUpset", "tickets", "predictionStage"):
                self.assertIn(key, legacy)

    def test_final_preserves_prediction_pre_and_review_flow(self):
        payload = self.engine.apply_preliminary_daily(self.payload())
        before = deepcopy(payload["races"][4]["predictionPre"])
        self.engine.apply_final_race(payload, 5, DIRECT, EX, OG)
        race = payload["races"][4]
        self.assertEqual(race["predictionPre"], before)
        self.assertEqual(race["predictionFinal"]["probabilityReviewStatus"], "reviewed")
        self.assertEqual(
            race["predictionFinal"]["probabilityFlow"],
            {"reviewed": True, "actualCourseRemapped": True},
        )
        self.assertEqual(payload["preds"]["5"]["predictionPre"], {k: before[k] for k in ("win", "second", "third")})
        self.assertEqual(payload["preds"]["5"]["predictionStage"], "final")

    def test_invalid_race_count_fails_closed(self):
        payload = self.payload()
        payload["races"].pop()
        with self.assertRaisesRegex(RuntimeError, "12R payload gate"):
            self.engine.apply_preliminary_daily(payload)

    def test_production_routes_have_no_v5_reference(self):
        production_paths = (
            ROOT / ".github" / "workflows" / "morning-data.yml",
            ROOT / "scripts" / "live_fetch_once.py",
            ROOT / "automation" / "run_shimonoseki_v6.py",
            ROOT / "automation" / "apply_shimonoseki_live_v6.py",
            ROOT / "engines" / "shimonoseki_v6" / "shimonoseki_engine_v6.py",
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in production_paths)
        for forbidden in (
            "run_shimonoseki_v5.py",
            "apply_shimonoseki_live_v5.py",
            "engines.shimonoseki_v5",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
