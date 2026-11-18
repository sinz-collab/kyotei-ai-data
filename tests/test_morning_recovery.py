from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
sys.path.insert(0, str(AUTOMATION))

import prepare_morning_recovery_cache as cache  # noqa: E402
import protect_manual_recovery_predictions as protection  # noqa: E402
import fetch_one  # noqa: E402
import write_morning_fetch_status as failure_status  # noqa: E402


class MorningRecoveryTest(unittest.TestCase):
    def test_timeout_status_counts_preserved_complete_race_triplets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            races = output / "races"
            races.mkdir()
            for race_no in (1, 2):
                for suffix in ("entry", "data", "motor"):
                    (races / f"race_{race_no:02d}_{suffix}.txt").write_text(
                        suffix, encoding="utf-8"
                    )
            (races / "race_03_entry.txt").write_text("entry", encoding="utf-8")

            self.assertEqual(failure_status.count_existing_races(output), 2)

    def test_partial_cache_resumes_at_first_incomplete_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            races = output / "races"
            races.mkdir()
            for race_no in (1, 2):
                prefix = races / f"race_{race_no:02d}"
                prefix.with_name(prefix.name + "_entry.txt").write_text(
                    "entry" * 101, encoding="utf-8"
                )
                prefix.with_name(prefix.name + "_data.txt").write_text(
                    "data", encoding="utf-8"
                )
                prefix.with_name(prefix.name + "_motor.txt").write_text(
                    "motor", encoding="utf-8"
                )

            self.assertEqual(fetch_one.first_incomplete_race(output), 3)

    def test_partial_race_is_retried_instead_of_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            races = output / "races"
            races.mkdir()
            (races / "race_01_entry.txt").write_text(
                "entry" * 101, encoding="utf-8"
            )
            (races / "race_01_data.txt").write_text("data", encoding="utf-8")

            self.assertEqual(fetch_one.first_incomplete_race(output), 1)

    def test_workflow_keeps_automatic_schedule_and_adds_manual_scopes(self) -> None:
        workflow = (ROOT / ".github/workflows/morning-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "5 21 * * *"', workflow)
        self.assertIn("recovery-${{ inputs.scope }}", workflow)
        self.assertIn("- failed", workflow)
        self.assertIn("- all", workflow)
        self.assertIn("Recover venues sequentially", workflow)
        self.assertIn("group: morning-race-data", workflow)

    def test_executor_has_live_guard_global_lock_and_tighter_limits(self) -> None:
        executor = (ROOT / "deploy/morning-vps/sinz-morning-exec").read_text(
            encoding="utf-8"
        )
        for expected in (
            "recovery-all|recovery-failed",
            "sinz-live-fetch.service",
            "MemAvailable",
            'recovery.lock',
            "cpu_quota=25%",
            "memory_max=1024M",
            "venue_timeout=240",
            "prepare_morning_recovery_cache.py",
            "--reason recovery_timeout --count-existing",
            'exit "$selection_rc"',
            "cached artifacts were left unchanged",
        ):
            self.assertIn(expected, executor)
        self.assertEqual(executor.count("awk -v load_one="), 2)
        self.assertNotIn("awk -v load=", executor)
        gateway = (ROOT / "deploy/morning-vps/sinz-morning-gateway").read_text(
            encoding="utf-8"
        )
        self.assertIn("dummy|fetch|recovery-all|recovery-failed", gateway)
        self.assertIn('[ "$original_command" = version ]', gateway)
        self.assertIn('sha256sum "$executor"', gateway)
        self.assertIn("prepare_morning_recovery_cache.py", gateway)
        self.assertIn("validate_morning_vps_artifacts.py", gateway)
        workflow = (ROOT / ".github/workflows/morning-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("Verify deployed VPS executor version"), 2)

    def test_complete_cache_is_reused_without_fetch_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cache" / "桐生" / "20261118"
            destination = root / "run" / "桐生" / "20261118"
            source.mkdir(parents=True)
            (source / "fetch_status.json").write_text("{}", encoding="utf-8")
            with patch.object(cache, "cache_is_complete", return_value=True):
                cache.copy_cached(source, destination)
            self.assertTrue((destination / "fetch_status.json").is_file())

    def test_failed_scope_does_not_select_known_non_running_venue(self) -> None:
        self.assertFalse(
            cache.is_failed_status(
                {"precheck": {"open": False, "reason": "not_scheduled"}}
            )
        )
        self.assertTrue(
            cache.is_failed_status(
                {"failure": {"reason": "fetch_incomplete"}}
            )
        )

    def test_prior_prediction_and_history_are_preserved_exactly(self) -> None:
        prior = {
            "date": "2026-11-18",
            "preds": {
                "1": {
                    "normalTickets": ["1-2-3"],
                    "prediction_history": {"morning": [{"revision": 1}]},
                }
            },
            "races": [
                {
                    "race": 1,
                    "deadline": "10:00",
                    "prediction": {"normalTickets": ["1-2-3"]},
                }
            ],
        }
        current = {
            "date": "2026-11-18",
            "preds": {"1": {"normalTickets": ["6-5-4"]}},
            "races": [
                {
                    "race": 1,
                    "deadline": "10:00",
                    "prediction": {"normalTickets": ["6-5-4"]},
                }
            ],
        }
        result = protection.protect_payload(
            current,
            prior,
            "failed",
            datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        )
        self.assertEqual(result["preds"]["1"], prior["preds"]["1"])
        self.assertEqual(
            result["races"][0]["prediction"], prior["races"][0]["prediction"]
        )

    def test_new_post_deadline_prediction_is_distinct_and_not_in_history(self) -> None:
        current = {
            "date": "2026-11-18",
            "preds": {
                "1": {
                    "normalTickets": ["1-2-3"],
                    "prediction_history": {"morning": [{"revision": 1}]},
                }
            },
            "races": [
                {
                    "race": 1,
                    "deadline": "10:00",
                    "prediction": {
                        "normalTickets": ["1-2-3"],
                        "prediction_history": {"morning": [{"revision": 1}]},
                    },
                }
            ],
        }
        result = protection.protect_payload(
            current,
            None,
            "all",
            datetime(2026, 11, 18, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        )
        prediction = result["preds"]["1"]
        self.assertEqual(prediction["generationContext"], "manual_recovery_post_deadline")
        self.assertFalse(prediction["performanceEligible"])
        self.assertNotIn("prediction_history", prediction)
        self.assertEqual(result["manualRecovery"]["postDeadlineRaces"], [1])


if __name__ == "__main__":
    unittest.main()
