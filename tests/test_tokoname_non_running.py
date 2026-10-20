from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from automation import run_tokoname_v1 as runner


class TokonameNonRunningTests(unittest.TestCase):
    DATE = "2026-10-21"

    def write_report(self, data_root: Path, venue: dict, date: str | None = None) -> None:
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "morning_report.json").write_text(
            json.dumps(
                {
                    "date": date or self.DATE,
                    "venues": {"tokoname": venue},
                }
            ),
            encoding="utf-8",
        )

    def invoke(self, data_root: Path) -> tuple[int, str]:
        output = io.StringIO()
        argv = [
            "run_tokoname_v1.py",
            "--date",
            self.DATE,
            "--stage",
            "preliminary",
            "--write",
            "--data-root",
            str(data_root),
        ]
        with patch.object(sys, "argv", argv), redirect_stdout(output):
            result = runner.main()
        return result, output.getvalue()

    def test_missing_json_on_confirmed_non_running_day_exits_zero(self) -> None:
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

            result, output = self.invoke(data_root)

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output)["status"], "not_running")

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

    def test_missing_json_after_fetch_failure_is_an_error(self) -> None:
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

    def test_missing_json_without_current_status_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"

            with self.assertRaisesRegex(FileNotFoundError, "availability=unknown"):
                self.invoke(data_root)

    def test_existing_json_runs_the_unchanged_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            path = data_root / "venues" / "tokoname" / "20261021.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")

            with patch.object(runner.tokoname_site_pipeline, "main", return_value=0) as pipeline:
                result, _ = self.invoke(data_root)

            self.assertEqual(result, 0)
            pipeline.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
