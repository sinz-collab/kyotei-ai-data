from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from validate_morning_regression import validate
import build_site_data as morning_builder


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
                "deadline": "09:00",
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

    def test_morning_main_uses_isolated_output_and_preserves_existing_domains(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "source"
            data_root = root / "output"
            status_dir = source_root / "A" / "20260724"
            status_dir.mkdir(parents=True)
            (status_dir / "fetch_status.json").write_text(
                json.dumps(
                    {
                        "date": "2026-07-24",
                        "slug": "toda",
                        "name": "A",
                        "open": True,
                        "entryCount": 12,
                        "precheck": {"reason": "race_page_found"},
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "venues.json"
            config_path.write_text(
                json.dumps({"venues": [{"slug": "toda", "name": "A"}]}),
                encoding="utf-8",
            )
            existing = payload()
            existing["preds"]["1"]["marker"] = "keep"
            venue_dir = data_root / "venues" / "toda"
            venue_dir.mkdir(parents=True)
            for filename in ("20260724.json", "latest.json"):
                (venue_dir / filename).write_text(json.dumps(existing), encoding="utf-8")
            morning = deepcopy(existing)
            morning["engine"] = ""
            morning["eventDay"] = 1
            morning["eventDayLabel"] = "初日"
            morning["preds"] = {}
            for race in morning["races"]:
                for racer in race["racers"]:
                    racer["season_runs"] = []

            with (
                patch.object(morning_builder, "CONFIG_PATH", config_path),
                patch.object(morning_builder, "ALL_VENUES", [("toda", "A")]),
                patch.object(
                    morning_builder,
                    "build_payload",
                    return_value=(morning, {"reason": "ok"}),
                ),
                patch.object(
                    sys,
                    "argv",
                    [
                        "build_site_data.py",
                        "--date",
                        "2026-07-24",
                        "--source-root",
                        str(source_root),
                        "--data-root",
                        str(data_root),
                    ],
                ),
            ):
                self.assertEqual(morning_builder.main(), 0)

            after = json.loads((venue_dir / "20260724.json").read_text(encoding="utf-8"))
            self.assertEqual(after["eventDay"], 2)
            self.assertEqual(after["preds"]["1"]["marker"], "keep")
            self.assertTrue(after["preds"]["1"]["ai"])
            self.assertTrue(after["preds"]["1"]["realtime"])
            self.assertTrue(after["preds"]["1"]["odds"])
            self.assertEqual(after["preds"]["1"]["result"]["status"], "ok")
            self.assertTrue(after["races"][0]["racers"][0]["season_runs"])


if __name__ == "__main__":
    unittest.main()
