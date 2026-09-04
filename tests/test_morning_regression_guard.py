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

from validate_morning_regression import PREDICTION_VENUES, validate
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


def payload(date: str = "2026-07-24", event_day: int = 2) -> dict:
    races = []
    for race in range(1, 13):
        racers = [
            {
                "lane": lane,
                "name": f"Racer {lane}",
                "season_runs": [{"race": "1R", "finish": "2着"}],
                "season_groups": [],
            }
            for lane in range(1, 7)
        ]
        races.append(
            {
                "race": race,
                "deadline": "09:00",
                "racers": racers,
                "setsukan": [
                    {
                        "lane": racer["lane"],
                        "season_runs": deepcopy(racer["season_runs"]),
                        "season_groups": [],
                    }
                    for racer in racers
                ],
            }
        )
    return {
        "venueId": "toda",
        "date": date,
        "engine": "venue_engine",
        "eventDay": event_day,
        "races": races,
        "preds": {str(race): prediction() for race in range(1, 13)},
    }


def mark_no_prior_meeting_runs(value: dict, race_index: int = 0, lane: int = 4) -> None:
    racer = value["races"][race_index]["racers"][lane - 1]
    row = value["races"][race_index]["setsukan"][lane - 1]
    racer["name"] = "Additional Racer"
    racer["season_runs"] = []
    racer["season_groups"] = []
    evidence = {
        "source": "published_prior_meeting_data",
        "venue": "toda",
        "checked_dates": ["2026-07-23"],
        "checked_event_days": [1],
        "prior_race_appearances": 0,
    }
    racer["setsukan_status"] = "no_prior_meeting_runs"
    racer["setsukan_evidence"] = deepcopy(evidence)
    racer["setsukan_first_entry_date"] = "2026-07-24"
    row.update(
        {
            "season_runs": [],
            "season_groups": [],
            "setsukan_status": "no_prior_meeting_runs",
            "setsukan_evidence": deepcopy(evidence),
            "setsukan_first_entry_date": "2026-07-24",
        }
    )


class MorningRegressionGuardTests(unittest.TestCase):
    def test_fukuoka_is_registered_prediction_venue(self) -> None:
        self.assertIn("fukuoka", PREDICTION_VENUES)

    def write_tree(self, root: Path, value: dict, prior: dict | None = None) -> None:
        venue = root / "venues" / "toda"
        venue.mkdir(parents=True)
        filename = value["date"].replace("-", "") + ".json"
        (venue / filename).write_text(json.dumps(value), encoding="utf-8")
        if prior is not None:
            prior_filename = prior["date"].replace("-", "") + ".json"
            (venue / prior_filename).write_text(json.dumps(prior), encoding="utf-8")
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "venues": [
                        {
                            "slug": "toda",
                            "open": True,
                            "predictionStatus": "ready",
                            "dataPath": f"venues/toda/{filename}",
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

    def test_prediction_history_or_active_stage_change_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before_root = root / "before"
            after_root = root / "after"
            before = payload()
            before["preds"]["1"]["prediction_history"] = {
                "morning": [{"revision": 1, "content_sha256": "keep"}]
            }
            before["preds"]["1"]["active_prediction_stage"] = "morning"
            after = deepcopy(before)
            after["preds"]["1"]["prediction_history"]["morning"] = []
            after["preds"]["1"]["active_prediction_stage"] = "final"
            self.write_tree(before_root, before)
            self.write_tree(after_root, after)
            errors = validate(before_root, after_root)
            self.assertTrue(any("prediction history changed" in error for error in errors))
            self.assertTrue(any("active prediction stage changed" in error for error in errors))

    def test_second_day_empty_setsukan_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            broken = payload()
            broken["races"][0]["racers"][3]["season_runs"] = []
            broken["races"][0]["setsukan"][3]["season_runs"] = []
            self.write_tree(after, broken)
            with patch("builtins.print") as mock_print:
                errors = validate(before, after)
            self.assertEqual(errors, [])
            mock_print.assert_called_once_with("WARNING: toda 1R: setsukan missing lanes")

    def test_verified_no_prior_meeting_runs_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            current = payload()
            mark_no_prior_meeting_runs(current)
            self.write_tree(after, current, payload("2026-07-23", 1))
            self.assertEqual(validate(before, after), [])

    def test_no_prior_status_without_evidence_warns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            current = payload()
            mark_no_prior_meeting_runs(current)
            current["races"][0]["racers"][3].pop("setsukan_evidence")
            self.write_tree(after, current, payload("2026-07-23", 1))
            with patch("builtins.print") as mock_print:
                errors = validate(before, after)
            self.assertEqual(errors, [])
            mock_print.assert_called_once_with("WARNING: toda 1R: setsukan missing lanes")

    def test_no_prior_status_warns_when_racer_appeared_on_prior_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            current = payload()
            mark_no_prior_meeting_runs(current)
            prior = payload("2026-07-23", 1)
            prior["races"][0]["racers"][0]["name"] = "Additional Racer"
            self.write_tree(after, current, prior)
            with patch("builtins.print") as mock_print:
                errors = validate(before, after)
            self.assertEqual(errors, [])
            mock_print.assert_called_once_with("WARNING: toda 1R: setsukan missing lanes")

    def test_setsukan_with_fewer_than_six_lanes_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            before, after = root / "before", root / "after"
            self.write_tree(before, payload())
            broken = payload()
            broken["races"][0]["setsukan"].pop()
            self.write_tree(after, broken)
            errors = validate(before, after)
            self.assertTrue(any("setsukan lanes invalid" in error for error in errors))

    def test_attach_independent_domains_always_emits_six_setsukan_lanes(self) -> None:
        value = payload()
        value["races"][0]["racers"][3]["season_runs"] = []
        value["races"][0]["racers"][3]["season_groups"] = []
        result = morning_builder.attach_independent_race_domains(
            value,
            "toda",
            True,
            "",
        )
        for race in result["races"]:
            self.assertEqual(
                [row["lane"] for row in race["setsukan"]],
                list(range(1, 7)),
            )

    def test_builder_marks_only_racers_absent_from_complete_prior_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data_root = Path(temp)
            venue_dir = data_root / "venues" / "toda"
            venue_dir.mkdir(parents=True)
            prior = payload("2026-07-23", 1)
            (venue_dir / "20260723.json").write_text(
                json.dumps(prior),
                encoding="utf-8",
            )
            current = payload()
            additional = current["races"][0]["racers"][3]
            additional["name"] = "Additional Racer"
            additional["season_runs"] = []
            additional["season_groups"] = []
            appeared = current["races"][0]["racers"][4]
            appeared["season_runs"] = []
            appeared["season_groups"] = []

            morning_builder.annotate_no_prior_meeting_runs(current, "toda", data_root)

            self.assertEqual(additional["season_runs"], [])
            self.assertEqual(additional["season_groups"], [])
            self.assertEqual(additional["setsukan_status"], "no_prior_meeting_runs")
            self.assertEqual(
                additional["setsukan_first_entry_date"],
                "2026-07-24",
            )
            self.assertNotIn("setsukan_status", appeared)

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
            existing["preds"]["1"]["prediction_history"] = {
                "morning": [{"revision": 1, "content_sha256": "morning"}],
                "t15": [{"revision": 1, "content_sha256": "t15"}],
                "t5": [],
                "final": [],
                "unknown": [],
            }
            existing["preds"]["1"]["active_prediction_stage"] = "t15"
            venue_dir = data_root / "venues" / "toda"
            venue_dir.mkdir(parents=True)
            for filename in ("20260724.json", "latest.json"):
                (venue_dir / filename).write_text(json.dumps(existing), encoding="utf-8")
            morning = deepcopy(existing)
            morning["venueId"] = "toda"
            morning["venue"] = "A"
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
            self.assertEqual(
                after["preds"]["1"]["prediction_history"],
                existing["preds"]["1"]["prediction_history"],
            )
            self.assertEqual(after["preds"]["1"]["active_prediction_stage"], "t15")
            self.assertTrue(after["races"][0]["racers"][0]["season_runs"])


if __name__ == "__main__":
    unittest.main()
