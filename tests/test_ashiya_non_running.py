from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "automation" / "run_ashiya_v16.py"


def load_runner():
    engine_package = types.ModuleType("ashiya_engine")
    engine_module = types.ModuleType("ashiya_engine.engine")
    engine_module.AshiyaEngine = object
    prediction_module = types.ModuleType("predict_venue_json")
    prediction_module.merge_race_payload = lambda payload, race: race

    with patch.dict(
        sys.modules,
        {
            "ashiya_engine": engine_package,
            "ashiya_engine.engine": engine_module,
            "predict_venue_json": prediction_module,
        },
    ):
        spec = importlib.util.spec_from_file_location(
            "run_ashiya_v16_non_running_test",
            RUNNER_PATH,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module


class AshiyaNonRunningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = load_runner()

    def invoke(self, data_root: Path) -> tuple[int, str]:
        output = io.StringIO()
        argv = [
            str(RUNNER_PATH),
            "--date",
            "2026-08-16",
            "--data-root",
            str(data_root),
        ]

        with patch.object(sys, "argv", argv), redirect_stdout(output):
            result = self.runner.main()

        return result, output.getvalue()

    def write_report(self, data_root: Path, venue: dict) -> None:
        data_root.mkdir(parents=True, exist_ok=True)
        (data_root / "morning_report.json").write_text(
            json.dumps(
                {
                    "date": "2026-08-16",
                    "venues": {"ashiya": venue},
                }
            ),
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

            result, output = self.invoke(data_root)

            expected_path = data_root / "venues" / "ashiya" / "20260816.json"
            self.assertEqual(result, 0)
            self.assertIn(f"Ashiya data is not open: {expected_path}", output)
            self.assertFalse(expected_path.exists())

    def test_missing_json_without_metadata_keeps_existing_skip_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / "data"
            result, output = self.invoke(data_root)

            self.assertEqual(result, 0)
            self.assertIn("Ashiya data is not open:", output)

    def test_missing_json_while_venue_is_open_is_an_error(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
