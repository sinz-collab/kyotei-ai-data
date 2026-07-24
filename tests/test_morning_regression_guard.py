from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from validate_morning_regression import validate


def prediction() -> dict:
    probabilities = {str(lane): 100 / 6 for lane in range(1, 7)}
    return {
        "win": probabilities,
        "second": probabilities,
        "third": probabilities,
        "sab": "A",
        "ai": [{"combo": "1-2-3"}],
        "realtime": {"last": {"1": {"time": 6.70}}},
        "odds": {"1-2-3": 12.3},
        "result": {"status": "ok", "order": "1-2-3"},
    }


def payload() -> dict:
    return {
        "date": "2026-07-24",
        "engine": "venue_engine",
        "eventDay": 2,
        "races": [
            {
                "race": race,
                "racers": [
                    {"lane": lane, "season_runs": [{"race": "1R", "finish": "2着"}]}
                    for lane in range(1, 7)
                ],
            }
            for race in range(1, 13)
        ],
        "preds": {str(race): prediction() for race in range(1, 13)},
    }


class MorningRegressionGuardTests(unittest.TestCase):
    def write_tree(self, root: Path, value: dict) -> None:
        venue = root / "venues" / "toda"
        venue.mkdir(parents=True)
        (venue / "20260724.json").write_text(json.dumps(value), encoding="utf-8")
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "venues": [
                        {
                            "slug": "toda",
                            "open": True,
                            "predictionStatus": "ready",
                            "dataPath": "venues/toda/20260724.json",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

    def test_unchanged_domains_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            self.write_tree(after, payload())
            self.assertEqual(validate(before, after), [])

    def test_baseline_empty_tickets_and_domain_loss_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            broken = payload()
            broken["engine"] = "deterministic_baseline_v1"
            for item in broken["preds"].values():
                item["ai"] = []
                item.pop("realtime")
                item.pop("odds")
                item.pop("result")
            self.write_tree(after, broken)
            errors = validate(before, after)
            self.assertTrue(any("deterministic_baseline_v1" in error for error in errors))
            self.assertTrue(any("tickets disappeared" in error for error in errors))
            self.assertTrue(any("result disappeared" in error for error in errors))
            self.assertTrue(any("live data disappeared" in error for error in errors))
            self.assertTrue(any("odds disappeared" in error for error in errors))

    def test_second_day_empty_setsukan_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            broken = payload()
            broken["races"][0]["racers"][3]["season_runs"] = []
            self.write_tree(after, broken)
            self.assertTrue(any("setsukan missing" in error for error in validate(before, after)))


if __name__ == "__main__":
    unittest.main()
