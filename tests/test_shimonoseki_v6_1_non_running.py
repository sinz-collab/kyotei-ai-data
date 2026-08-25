from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from engines.shimonoseki_v6_1 import run_shimonoseki_v6_1 as runner


ROOT = Path(__file__).resolve().parents[1]


class ShimonosekiV61NonRunningTest(unittest.TestCase):
    DATE = "2026-08-25"

    def invoke(self, data_root: Path) -> tuple[int, dict]:
        output = io.StringIO()
        argv = [
            "run_shimonoseki_v6_1.py",
            "--date",
            self.DATE,
            "--stage",
            "preliminary",
            "--data-root",
            str(data_root),
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            result = runner.main()
        return result, json.loads(output.getvalue())

    def write_report(self, data_root: Path, venue: dict) -> None:
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "morning_report.json").write_text(
            json.dumps({"date": self.DATE, "venues": {"shimonoseki": venue}}),
            encoding="utf-8",
        )

    def test_missing_json_on_non_running_day_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            self.write_report(
                data_root,
                {
                    "open": False,
                    "raceDataAvailable": False,
                    "predictionStatus": "not_running",
                    "detail": {"reason": "not_scheduled"},
                },
            )
            latest = data_root / "venues" / "shimonoseki" / "latest.json"
            latest.parent.mkdir(parents=True)
            previous_latest = {"venueId": "shimonoseki", "date": "2026-08-24"}
            latest.write_text(json.dumps(previous_latest), encoding="utf-8")

            result, output = self.invoke(data_root)

            self.assertEqual(result, 0)
            self.assertEqual(output["status"], "not_running")
            self.assertEqual(output["availability"], "not_open")
            self.assertEqual(output["reason"], "not_scheduled")
            self.assertFalse(
                (data_root / "venues" / "shimonoseki" / "20260825.json").exists()
            )
            self.assertEqual(json.loads(latest.read_text(encoding="utf-8")), previous_latest)

    def test_automation_wrapper_non_running_exit_code_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            self.write_report(
                data_root,
                {
                    "open": False,
                    "raceDataAvailable": False,
                    "predictionStatus": "not_running",
                    "detail": {"reason": "not_scheduled"},
                },
            )

            process = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "automation" / "run_shimonoseki_v6_1.py"),
                    "--date",
                    self.DATE,
                    "--stage",
                    "preliminary",
                    "--data-root",
                    str(data_root),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout)["status"], "not_running")

    def test_missing_json_while_scheduled_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            self.write_report(
                data_root,
                {
                    "open": True,
                    "raceDataAvailable": True,
                    "predictionStatus": "unavailable",
                    "detail": {"reason": "prediction_payload_unavailable"},
                },
            )

            with self.assertRaisesRegex(FileNotFoundError, "availability=open"):
                self.invoke(data_root)

    def test_missing_json_after_precheck_failure_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            self.write_report(
                data_root,
                {
                    "open": False,
                    "raceDataAvailable": False,
                    "predictionStatus": "not_running",
                    "detail": {"reason": "precheck_failed:TimeoutError"},
                },
            )

            with self.assertRaisesRegex(FileNotFoundError, "availability=fetch_failed"):
                self.invoke(data_root)

    def test_missing_json_without_current_metadata_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"

            with self.assertRaisesRegex(FileNotFoundError, "availability=unknown"):
                self.invoke(data_root)


if __name__ == "__main__":
    unittest.main()
