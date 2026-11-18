from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
sys.path.insert(0, str(AUTOMATION))

import fetch_one  # noqa: E402
import prepare_morning_artifacts as artifact  # noqa: E402
import validate_morning_vps_artifacts as validator  # noqa: E402


class MorningVpsProductionTest(unittest.TestCase):
    def test_complete_partial_cache_is_promoted_without_refetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            races = root / "桐生" / "20261118" / "races"
            races.mkdir(parents=True)
            for race_no in range(1, 13):
                (races / f"race_{race_no:02d}_entry.txt").write_text(
                    "entry" * 101, encoding="utf-8"
                )
                (races / f"race_{race_no:02d}_data.txt").write_text(
                    "data", encoding="utf-8"
                )
                (races / f"race_{race_no:02d}_motor.txt").write_text(
                    "motor", encoding="utf-8"
                )
            with (
                patch.object(sys, "argv", [
                    "fetch_one.py", "--venue", "kiryu",
                    "--date", "2026-11-18", "--root", str(root),
                ]),
                patch.object(
                    fetch_one,
                    "venue_is_open",
                    return_value={"open": True, "reason": "race_page_found"},
                ),
                patch.object(fetch_one.subprocess, "run") as run,
                patch.object(fetch_one, "fetch_tide") as tide,
            ):
                self.assertEqual(fetch_one.main(), 0)
            run.assert_not_called()
            tide.assert_not_called()
            status = json.loads(
                (root / "桐生" / "20261118" / "fetch_status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(status["open"])
            self.assertEqual(status["entryCount"], 12)

    def test_guard_status_is_artifact_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(
                [
                    sys.executable,
                    str(AUTOMATION / "write_morning_fetch_status.py"),
                    "--venue", "kiryu",
                    "--date", "2026-11-18",
                    "--root", str(root),
                    "--reason", "cutoff_before_live",
                ],
                check=True,
                cwd=ROOT,
            )
            status = json.loads(
                (root / "桐生" / "20261118" / "fetch_status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                (status["slug"], status["name"], status["date"]),
                ("kiryu", "桐生", "2026-11-18"),
            )
            self.assertEqual(status["failure"]["reason"], "cutoff_before_live")

    def test_executor_enforces_window_deadline_and_live_priority(self) -> None:
        executor = (ROOT / "deploy/morning-vps/sinz-morning-exec").read_text(
            encoding="utf-8"
        )
        for expected in (
            "06:00 through 06:45 JST",
            'target_date 08:15',
            'target_date 08:18',
            'cpu_quota=50%',
            'memory_max=1536M',
            'nice_level=10',
            'io_weight=10',
            'security-challenge-$target_date',
            "security_challenge_global_stop",
            "*_entry.html",
        ):
            self.assertIn(expected, executor)

    def test_workflow_has_one_jst_schedule_and_no_github_fetch_runtime(self) -> None:
        workflow = (ROOT / ".github/workflows/morning-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('cron: "5 21 * * *"', workflow)
        self.assertIn("06:00-06:45 JST", workflow)
        self.assertIn('group: morning-race-data', workflow)
        self.assertIn("validate_morning_vps_artifacts.py", workflow)
        self.assertNotIn("python automation/fetch_one.py --venue", workflow)

    def test_security_challenge_is_not_retried_or_followed_by_tide_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            completed = type(
                "Completed", (), {"returncode": fetch_one.SECURITY_CHALLENGE_EXIT}
            )()
            with (
                patch.object(sys, "argv", [
                    "fetch_one.py", "--venue", "tokoname",
                    "--date", "2026-11-18", "--root", str(root),
                ]),
                patch.object(
                    fetch_one,
                    "venue_is_open",
                    return_value={"open": True, "reason": "race_page_found"},
                ),
                patch.object(fetch_one.subprocess, "run", return_value=completed) as run,
                patch.object(fetch_one, "fetch_tide") as tide,
            ):
                self.assertEqual(fetch_one.main(), fetch_one.SECURITY_CHALLENGE_EXIT)
            self.assertEqual(run.call_count, 1)
            tide.assert_not_called()
            status = json.loads(
                (root / "常滑" / "20261118" / "fetch_status.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(status["failure"]["reason"], "security_challenge")

    def test_required_racer_fields_include_registration_number(self) -> None:
        racer = {key: "x" for key in validator.REQUIRED_RACER_FIELDS}
        payload = {
            "races": [
                {"race": race, "racers": [dict(racer) for _ in range(6)]}
                for race in range(1, 13)
            ]
        }
        self.assertEqual(validator.payload_error(payload, {"reason": "ok"}), "")
        del payload["races"][0]["racers"][0]["player_id"]
        self.assertIn("player_id", validator.payload_error(payload, {"reason": "ok"}))

    def test_entry_html_is_part_of_the_artifact_contract(self) -> None:
        relative = Path("常滑") / "20261118" / "races" / "race_01_entry.html"
        self.assertTrue(artifact.allowed_relative_path(relative, "20261118"))


if __name__ == "__main__":
    unittest.main()
