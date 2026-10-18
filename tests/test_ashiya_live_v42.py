from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from ashiya_live_v42 import apply_ashiya_live_v42  # noqa: E402


def race_payload() -> dict:
    racers = []
    for lane in range(1, 7):
        racers.append({
            "lane": lane,
            "actual_course": lane,
            "nat_win": 7.0 - lane * .35,
            "local_win": 6.6 - lane * .25,
            "motor_2": 44 - lane * 2,
            "boat_2": 42 - lane,
            "exhibition_time": 6.72 + lane * .02,
            "start_timing": .08 + lane * .015,
            "lap_time": 36.55 + lane * .08,
            "straight_time": 7.45 + lane * .03,
            "turn_time": 7.55 + lane * .04,
            "season_runs": [
                {"finish": f"{min(4, lane)}着", "st": f".{9 + lane:02d}"},
                {"finish": f"{min(4, 7-lane)}着", "st": f".{11 + lane:02d}"},
            ],
        })
    return {"racers": racers, "actual_entry": [1, 2, 3, 4, 5, 6]}


def engine_result() -> dict:
    combinations = [
        "1-2-3", "1-2-4", "1-3-2", "1-3-4", "2-1-3",
        "2-3-1", "3-1-2", "3-2-1", "4-1-2", "5-1-2",
    ]
    rows = [{"combination": combo, "score": 1 / (index + 1)} for index, combo in enumerate(combinations)]
    return {
        "probabilities": [
            {"lane": lane, "win": probability}
            for lane, probability in enumerate([.42, .20, .14, .10, .08, .06], 1)
        ],
        "tickets": {
            "main": deepcopy(rows[:6]),
            "deviation": deepcopy(rows[6:8]),
            "upset": deepcopy(rows[8:]),
            "all": deepcopy(rows),
        },
        "audit": {},
    }


class AshiyaLiveV42Test(unittest.TestCase):
    def test_all_three_models_are_live_only_frozen_outputs(self) -> None:
        original = engine_result()
        actual = apply_ashiya_live_v42(original, race_payload())

        tickets = actual["tickets"]
        self.assertEqual([len(tickets[key]) for key in ("main", "deviation", "upset")], [6, 2, 2])
        all_combos = [row["combination"] for row in tickets["all"]]
        self.assertEqual(len(all_combos), 10)
        self.assertEqual(len(set(all_combos)), 10)
        old_combos = {row["combination"] for row in original["tickets"]["all"]}
        self.assertLessEqual(len(old_combos - set(all_combos)), 2)

        fly = actual["flyPrediction"]
        self.assertEqual(fly["status"], "live")
        self.assertEqual(len(fly["tickets"]), 10)
        fly_combos = [row["combo"] for row in fly["tickets"]]
        self.assertEqual(len(set(fly_combos)), 10)
        self.assertTrue(all(not combo.startswith("1-") for combo in fly_combos))
        self.assertGreaterEqual(actual["lane1FlyProbability"], 0)
        self.assertLessEqual(actual["lane1FlyProbability"], 100)
        self.assertTrue(actual["audit"]["ashiya_live_v42"]["flyProbability"]["complementAppliedOnce"])

    def test_odds_and_result_never_change_predictions(self) -> None:
        first_race = race_payload()
        second_race = deepcopy(first_race)
        first_race.update({"odds": {"1-2-3": 1.1}, "result": {"order": "1-2-3"}})
        second_race.update({"odds": {"6-5-4": 9999}, "result": {"order": "6-5-4"}})
        first = apply_ashiya_live_v42(engine_result(), first_race)
        second = apply_ashiya_live_v42(engine_result(), second_race)
        self.assertEqual(first["tickets"], second["tickets"])
        self.assertEqual(first["flyPrediction"], second["flyPrediction"])
        self.assertEqual(first["lane1FlyProbability"], second["lane1FlyProbability"])

    def test_models_fallback_independently(self) -> None:
        missing_original = race_payload()
        for racer in missing_original["racers"]:
            racer.pop("lap_time")
            racer.pop("straight_time")
            racer.pop("turn_time")
        original = engine_result()
        actual = apply_ashiya_live_v42(original, missing_original)
        self.assertEqual(actual["tickets"], original["tickets"])
        self.assertEqual(actual["flyPrediction"]["status"], "unavailable")
        self.assertIsInstance(actual["lane1FlyProbability"], float)

        unknown_groups = engine_result()
        unknown_groups["tickets"].pop("deviation")
        actual = apply_ashiya_live_v42(unknown_groups, race_payload())
        self.assertEqual(actual["audit"]["ashiya_live_v42"]["normal"]["status"], "fallback")
        self.assertEqual(actual["flyPrediction"]["status"], "live")
        self.assertIsInstance(actual["lane1FlyProbability"], float)

        malformed = engine_result()
        malformed["tickets"]["main"][0]["combination"] = "1-1-2"
        malformed["tickets"]["all"][0]["combination"] = "1-1-2"
        actual = apply_ashiya_live_v42(malformed, race_payload())
        self.assertEqual(actual["tickets"], malformed["tickets"])
        self.assertEqual(actual["audit"]["ashiya_live_v42"]["normal"]["reason"], "ticket_groups_invalid")

    def test_missing_exhibition_is_safe(self) -> None:
        race = race_payload()
        for racer in race["racers"]:
            racer.pop("exhibition_time")
            racer.pop("start_timing")
        original = engine_result()
        actual = apply_ashiya_live_v42(original, race)
        self.assertEqual(actual["tickets"], original["tickets"])
        self.assertEqual(actual["flyPrediction"]["tickets"], [])
        self.assertIsNone(actual["lane1FlyProbability"])

    def test_live_probability_is_exposed_without_second_complement(self) -> None:
        actual = apply_ashiya_live_v42(engine_result(), race_payload())
        self.assertEqual(
            actual["lane1FlyProbabilityLive"],
            actual["lane1FlyProbability"],
        )
        self.assertEqual(actual["lane1FlyProbabilityDisplayStage"], "live")
        self.assertFalse(actual["lane1FlyProbabilityIsProvisional"])


if __name__ == "__main__":
    unittest.main()
