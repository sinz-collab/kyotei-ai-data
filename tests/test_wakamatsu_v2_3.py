from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
sys.path.insert(0, str(AUTOMATION))

from apply_wakamatsu_v2_3 import apply_wakamatsu_v2_3
from wakamatsu_v2_3_adjustments import (
    FRONT_THIRD_BY_COURSE,
    _actual_entry_map,
    _slit_attack,
    _strong_attack,
    classify_tide_zone,
    normalize_tide_phase,
)


def load_day(day: str) -> dict:
    path = ROOT / "data" / "venues" / "wakamatsu" / f"{day.replace('-', '')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class WakamatsuV23UnitTest(unittest.TestCase):
    def test_tide_phase_normalizes_english_and_japanese(self):
        for value in ("rising", "上げ潮", "上げ", "上昇"):
            self.assertEqual(normalize_tide_phase(value), "rising")
        for value in ("falling", "下げ潮", "下げ", "下降"):
            self.assertEqual(normalize_tide_phase(value), "falling")

    def test_tide_zone_uses_sixty_minutes_inclusively(self):
        tide = {
            "events": [
                {"time": "10:00", "type": "low"},
                {"time": "16:00", "type": "満潮"},
            ]
        }
        self.assertEqual(classify_tide_zone("09:00", tide), "low_tide")
        self.assertEqual(classify_tide_zone("11:00", tide), "low_tide")
        self.assertEqual(classify_tide_zone("15:00", tide), "high_tide")
        self.assertEqual(classify_tide_zone("17:00", tide), "high_tide")
        self.assertEqual(classify_tide_zone("12:01", tide), "normal")

    def test_front_database_reference_values(self):
        self.assertAlmostEqual(FRONT_THIRD_BY_COURSE[3], 0.2188905547226387)
        self.assertAlmostEqual(FRONT_THIRD_BY_COURSE[6], 0.1769115442278860)
        self.assertAlmostEqual(FRONT_THIRD_BY_COURSE[2], 0.1709145427286357)
        self.assertAlmostEqual(FRONT_THIRD_BY_COURSE[5], 0.1529235382308846)

    def test_actual_entry_is_lane_to_real_course_and_has_priority(self):
        race = {
            "direct": {"actual_entry": [1, 3, 2, 4, 6, 5]},
            "exhibition": {
                "entries": [
                    {"lane": lane, "exhibition_course": lane}
                    for lane in range(1, 7)
                ]
            },
        }
        mapping, source = _actual_entry_map(race)
        self.assertEqual(source, "actual_entry")
        self.assertEqual(mapping, {1: 1, 3: 2, 2: 3, 4: 4, 6: 5, 5: 6})

    def test_strong_attack_requires_four_conditions_and_never_forces_top1(self):
        day = load_day("2026-09-23")
        generated = apply_wakamatsu_v2_3(day, "2026-09-23")
        final = generated["races"][0]["predictionFinal"]
        diagnostics = final["diagnostics"]["strongAttackOverride"]
        self.assertTrue(diagnostics["active"])
        self.assertTrue(all(row["matched"] >= 4 for row in diagnostics["candidates"] if row["active"]))
        self.assertFalse(diagnostics["top1Forced"])
        self.assertEqual(max(final["win"], key=final["win"].get), "1")

        self.assertFalse(generated["races"][8]["predictionFinal"]["diagnostics"]["strongAttackOverride"]["active"])

    def test_slit_attack_activation_and_non_activation(self):
        day = load_day("2026-09-23")
        generated = apply_wakamatsu_v2_3(day, "2026-09-23")
        for index, expected in ((6, True), (8, False)):
            final = generated["races"][index]["predictionFinal"]
            slit = final["diagnostics"]["slitAttackOverride"]
            self.assertEqual(slit["active"], expected)
            if expected:
                self.assertEqual(slit["attackLane"], 4)
                self.assertIn("4-5-1", [ticket["combo"] for ticket in final["tickets"]])
                self.assertNotEqual(final["sab"], "S")


class WakamatsuV23ReplayTest(unittest.TestCase):
    def test_20260923_saved_input_replay(self):
        day = load_day("2026-09-23")
        replay = apply_wakamatsu_v2_3(day, "2026-09-23")
        top_hits = ticket_hits = 0
        required = {
            2: "1-5-6",
            4: "1-3-4",
            5: "1-3-4",
            6: "1-4-2",
            7: "4-5-1",
            8: "1-3-6",
            10: "1-2-3",
            11: "1-3-6",
        }
        results = {
            1: "2-5-4", 2: "1-5-6", 3: "1-5-2", 4: "1-3-4",
            5: "1-3-4", 6: "1-4-2", 7: "4-5-1", 8: "1-3-6",
            9: "5-3-2", 10: "1-2-3", 11: "1-3-6", 12: "6-5-3",
        }
        for race in replay["races"]:
            final = race["predictionFinal"]
            actual = results[race["race"]]
            result = actual.split("-")
            tickets = [row["combo"] for row in final["tickets"]]
            top_hits += max(final["win"], key=final["win"].get) == result[0]
            ticket_hits += actual in tickets
            self.assertEqual(len(tickets), 10)
            self.assertEqual(len(set(tickets)), 10)
            self.assertIs(final["diagnostics"]["oddsUsedForPrediction"], False)
            self.assertIs(final["diagnostics"]["actualEntryAppliedBeforeBase"], True)
            for finish in ("win", "second", "third"):
                self.assertAlmostEqual(sum(final[finish].values()), 100.0, places=6)
            if race["race"] in required:
                self.assertIn(required[race["race"]], tickets)
        self.assertEqual(top_hits, 8)
        self.assertEqual(ticket_hits, 8)
        self.assertNotIn("5-3-2", [x["combo"] for x in replay["races"][8]["predictionFinal"]["tickets"]])
        self.assertNotIn("6-5-3", [x["combo"] for x in replay["races"][11]["predictionFinal"]["tickets"]])


if __name__ == "__main__":
    unittest.main()
