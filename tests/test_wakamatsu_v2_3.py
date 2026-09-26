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
    _apply_attack_link_corrections,
    _apply_escape_overweakening_guard,
    _actual_entry_map,
    _classify_full_reflection,
    _rank_tickets,
    _set_tickets,
    _slit_attack,
    _strong_attack,
    classify_tide_zone,
    normalize_tide_phase,
)


def load_day(day: str) -> dict:
    path = ROOT / "data" / "venues" / "wakamatsu" / f"{day.replace('-', '')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def ticket_prediction(
    grade: str = "A",
    fallback_count: int = 0,
    water_type: str = "通常型",
    scenarios: dict | None = None,
) -> dict:
    base_audit = {}
    for lane in range(1, 7):
        player_course = (
            {"available": False, "fallback": "course_prior"}
            if lane > 6 - fallback_count
            else {"available": True, "starts": 12}
        )
        base_audit[str(lane)] = {
            finish: {"player_course": deepcopy(player_course)}
            for finish in ("win", "second", "third")
        }
    return {
        "win": {"1": 68.0, "2": 12.0, "3": 10.0, "4": 5.0, "5": 3.0, "6": 2.0},
        "second": {"1": 24.0, "2": 23.0, "3": 20.0, "4": 14.0, "5": 11.0, "6": 8.0},
        "third": {"1": 10.0, "2": 18.0, "3": 22.0, "4": 20.0, "5": 16.0, "6": 14.0},
        "scenarios": scenarios or {},
        "raceContext": {"water_type": water_type},
        "sab": grade,
        "tickets": [],
        "diagnostics": {
            "fullReflection": {
                "status": "partial" if fallback_count else "complete",
                "inputAudit": {
                    "status": "partial" if fallback_count else "complete",
                    "missingCritical": ["playerCourseDb"] if fallback_count else [],
                },
                "baseAudit": base_audit,
            }
        },
    }


class WakamatsuV23UnitTest(unittest.TestCase):
    @staticmethod
    def probability_map(values):
        return {str(lane): value for lane, value in enumerate(values, 1)}

    @staticmethod
    def ranked_race():
        return {
            "live": {
                "exhibition": {"entries": [
                    {"lane": lane, "start_rank": lane, "exhibition_rank": lane}
                    for lane in range(1, 7)
                ]},
                "original": {"entries": [
                    {"lane": lane, "sum": 44.0 + lane / 10.0}
                    for lane in range(1, 7)
                ]},
            }
        }

    def test_escape_overweakening_guard_caps_at_four_points(self):
        race = self.ranked_race()
        prediction = {
            "win": self.probability_map([42.0, 13.0, 17.0, 14.0, 8.0, 6.0]),
            "diagnostics": {"escapeRateMultiAttack": {
                "active": True,
                "course1_win_delta": -0.08,
                "attackers": [
                    {"lane": 3, "share": 0.75},
                    {"lane": 4, "share": 0.25},
                ],
            }},
        }
        audit = _apply_escape_overweakening_guard(
            race, prediction, {"active": False}, {"active": False}
        )
        self.assertTrue(audit["active"])
        self.assertEqual(
            prediction["win"],
            self.probability_map([46.0, 13.0, 14.0, 13.0, 8.0, 6.0]),
        )
        self.assertAlmostEqual(sum(prediction["win"].values()), 100.0)

    def test_escape_guard_requires_every_condition(self):
        race = self.ranked_race()
        race["live"]["exhibition"]["entries"][0]["start_rank"] = 3
        prediction = {
            "win": self.probability_map([42.0, 13.0, 17.0, 14.0, 8.0, 6.0]),
            "diagnostics": {"escapeRateMultiAttack": {
                "active": True,
                "course1_win_delta": -0.08,
                "attackers": [{"lane": 3, "share": 1.0}],
            }},
        }
        before = deepcopy(prediction["win"])
        audit = _apply_escape_overweakening_guard(
            race, prediction, {"active": False}, {"active": False}
        )
        self.assertFalse(audit["active"])
        self.assertEqual(prediction["win"], before)

    def test_course3_and_course4_attack_links_use_actual_courses(self):
        race = self.ranked_race()
        entry = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}
        prediction = {
            "win": self.probability_map([45.0, 10.0, 16.0, 13.0, 9.0, 7.0]),
            "second": self.probability_map([20.0, 18.0, 17.0, 16.0, 15.0, 14.0]),
            "third": self.probability_map([13.0, 15.0, 17.0, 18.0, 19.0, 18.0]),
            "scenarios": {
                "makuri_3": 0.12, "makurizashi_3": 0.08,
                "makuri_4": 0.10, "makurizashi_4": 0.08,
            },
            "raceContext": {"water_type": "3攻め型"},
        }
        audits = _apply_attack_link_corrections(
            race,
            prediction,
            entry,
            {"active": True, "candidates": [{"active": True, "course": 4}]},
            {"active": False},
        )
        self.assertTrue(audits["course3"]["active"])
        self.assertTrue(audits["course4"]["active"])
        self.assertEqual(audits["course3"]["secondAddedPtByLane"], {"1": 1.5, "3": 1.0})
        self.assertEqual(audits["course3"]["thirdAddedPtByLane"], {"4": 1.5, "5": 1.5, "6": 1.5})
        self.assertEqual(audits["course4"]["thirdAddedPtByLane"], {"5": 1.5, "6": 1.5})
        self.assertAlmostEqual(sum(prediction["second"].values()), 100.0)
        self.assertAlmostEqual(sum(prediction["third"].values()), 100.0)

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

    def test_s_concentration_guard_uses_player_course_fallback_threshold(self):
        entry = {lane: lane for lane in range(1, 7)}
        scenarios = {"sashi_2": 0.16}
        for fallback_count, expected_applied, expected_grade in (
            (1, False, "S"),
            (2, True, "A"),
        ):
            prediction = ticket_prediction("S", fallback_count, scenarios=scenarios)
            baseline = [row["combo"] for row in _rank_tickets(prediction, entry)[:10]]
            self.assertEqual({combo.split("-")[0] for combo in baseline}, {"1"})
            _set_tickets(prediction, entry, {"active": False}, {"active": False})
            tickets = [row["combo"] for row in prediction["tickets"]]
            protection = prediction["diagnostics"]["ticketProtection"]
            self.assertEqual(protection["applied"], expected_applied)
            self.assertEqual(protection["effectiveTicketGrade"], expected_grade)
            self.assertEqual(tickets[:6], baseline[:6])
            self.assertEqual(len(tickets), 10)
            self.assertEqual(len(set(tickets)), 10)
            if expected_applied:
                self.assertEqual(sum(a != b for a, b in zip(tickets, baseline)), 1)
                self.assertIn(protection["replacedCombo"], baseline[8:10])
            else:
                self.assertEqual(tickets, baseline)

    def test_weak_development_condition_keeps_a_and_b_tickets_unchanged(self):
        entry = {lane: lane for lane in range(1, 7)}
        for grade in ("A", "B"):
            prediction = ticket_prediction(grade, scenarios={"sashi_2": 0.149999})
            baseline = [row["combo"] for row in _rank_tickets(prediction, entry)[:10]]
            _set_tickets(prediction, entry, {"active": False}, {"active": False})
            self.assertEqual([row["combo"] for row in prediction["tickets"]], baseline)
            self.assertFalse(prediction["diagnostics"]["ticketProtection"]["applied"])

    def test_three_attack_protection_survives_later_development_protection(self):
        entry = {lane: lane for lane in range(1, 7)}
        prediction = ticket_prediction(
            "A",
            fallback_count=2,
            water_type="3攻め型",
            scenarios={
                "sashi_2": 0.20,
                "makuri_3": 0.08,
                "makurizashi_3": 0.08,
            },
        )
        baseline = [row["combo"] for row in _rank_tickets(prediction, entry)[:10]]
        _set_tickets(prediction, entry, {"active": False}, {"active": False})
        tickets = [row["combo"] for row in prediction["tickets"]]
        protection = prediction["diagnostics"]["ticketProtection"]
        three_attack_step = next(
            step for step in protection["steps"] if step["reason"] == "three_attack_lane4_link"
        )
        self.assertIn(three_attack_step["addedCombo"], tickets)
        self.assertEqual(tickets[:6], baseline[:6])
        self.assertLessEqual(len(protection["steps"]), 2)
        self.assertTrue(all(step["replacedCombo"] in baseline[8:10] for step in protection["steps"]))

    def test_full_reflection_distinguishes_course_prior_fallback(self):
        prediction = ticket_prediction("A", fallback_count=2)
        _classify_full_reflection(prediction)
        reflection = prediction["diagnostics"]["fullReflection"]
        self.assertEqual(reflection["status"], "complete_with_fallback")
        self.assertEqual(reflection["playerCourseStatus"]["fallbackCount"], 2)

        prediction["diagnostics"]["fullReflection"]["inputAudit"]["missingCritical"].append("motor")
        _classify_full_reflection(prediction)
        self.assertEqual(prediction["diagnostics"]["fullReflection"]["status"], "partial")


class WakamatsuV23ReplayTest(unittest.TestCase):
    def test_20260926_races_1_to_11_contract(self):
        replay = apply_wakamatsu_v2_3(load_day("2026-09-26"), "2026-09-26")
        races = {int(race["race"]): race for race in replay["races"]}
        for race_no in range(1, 12):
            prediction = races[race_no].get("predictionFinal") or races[race_no]["prediction"]
            tickets = [row["combo"] for row in prediction["tickets"]]
            self.assertEqual(len(tickets), 10)
            self.assertEqual(len(set(tickets)), 10)
            self.assertIs(prediction["diagnostics"]["oddsUsedForPrediction"], False)
            for finish in ("win", "second", "third"):
                self.assertAlmostEqual(sum(prediction[finish].values()), 100.0, places=6)

    def test_saved_inputs_keep_probability_and_ticket_contracts(self):
        for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
            replay = apply_wakamatsu_v2_3(load_day(day), day)
            for race in replay["races"]:
                final = race["predictionFinal"]
                tickets = [row["combo"] for row in final["tickets"]]
                self.assertEqual(final["engine"], "wakamatsu_engine_v2.3")
                self.assertEqual(len(tickets), 10)
                self.assertEqual(len(set(tickets)), 10)
                self.assertIs(final["diagnostics"]["oddsUsedForPrediction"], False)
                self.assertIs(final["diagnostics"]["ticketProtection"]["oddsUsed"], False)
                for finish in ("win", "second", "third"):
                    self.assertAlmostEqual(sum(final[finish].values()), 100.0, places=6)

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
