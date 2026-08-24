from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from copy import deepcopy
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

    def test_pre_run_rebuilds_legacy_but_preserves_native_live_prediction(self) -> None:
        probabilities = {
            str(lane): 100.0 / 6.0
            for lane in range(1, 7)
        }
        tickets = [
            f"1-2-{index}"
            for index in range(10)
        ]
        native_live = {
            "stage": "live",
            "sab": {"grade": "A"},
            "marker": "keep",
        }
        source = {
            "preds": {
                str(race_no): {
                    "sab": "M",
                    "active_prediction_stage": "morning",
                    "prediction_history": {"morning": [{"revision": 1}]},
                }
                for race_no in range(1, 13)
            },
            "races": [
                {
                    "race": race_no,
                    "prediction": deepcopy(native_live),
                }
                for race_no in range(1, 13)
            ],
        }

        def site_prediction(_result, _race_no, _stage):
            return (
                {
                    "win": probabilities,
                    "second": probabilities,
                    "third": probabilities,
                    "sab": "A",
                    "tickets": tickets,
                },
                {"stage": "pre"},
            )

        with patch.object(
            self.runner,
            "build_site_prediction",
            side_effect=site_prediction,
        ):
            merged = self.runner.merge_predictions(
                source,
                {race_no: {} for race_no in range(1, 13)},
                "pre",
            )

        self.assertEqual(merged["preds"]["1"]["sab"], "A")
        self.assertEqual(merged["preds"]["1"]["active_prediction_stage"], "morning")
        self.assertEqual(
            merged["preds"]["1"]["prediction_history"],
            source["preds"]["1"]["prediction_history"],
        )
        self.assertEqual(merged["races"][0]["prediction"], native_live)


if __name__ == "__main__":
    unittest.main()
