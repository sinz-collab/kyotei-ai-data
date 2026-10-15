from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(AUTOMATION))

from automation import run_wakamatsu_v2_live as runner
from automation import run_wakamatsu_v2_3_live as runner23
from live_fetch_once import apply_wakamatsu_live_prediction
from wakamatsu_fly_tickets_v1 import build_fly_prediction


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prediction(phase: str) -> dict:
    return {
        "phase": phase,
        "finalPredictionStatus": "complete" if phase == "final" else "waiting_live_data",
        "engine": runner.ENGINE_ID,
        "engineVersion": runner.ENGINE_VERSION,
        "predictionStage": runner.FINAL_PREDICTION_STAGE if phase == "final" else None,
        "win": {str(lane): float(lane) for lane in range(1, 7)},
        "second": {str(lane): float(lane + 1) for lane in range(1, 7)},
        "third": {str(lane): float(lane + 2) for lane in range(1, 7)},
        "sab": "A",
        "tickets": [{"combo": f"1-2-{lane}"} for lane in range(1, 7)]
        + [{"combo": f"2-1-{lane}"} for lane in range(1, 5)],
        "diagnostics": {"oddsUsedForPrediction": False},
    }


class WakamatsuLiveRunnerTest(unittest.TestCase):
    def test_live_fetch_hook_runs_only_with_complete_wakamatsu_inputs(self):
        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b'{"status": "complete"}', b""

        target = {"venue": "wakamatsu", "date": "2026-08-25", "race_no": 3}
        complete = {
            "items": {
                "direct": {"complete": True, "status": "complete"},
                "exhibition": {"complete": True, "status": "complete"},
                "original_exhibition": {"complete": True, "status": "complete"},
            }
        }
        logger = unittest.mock.Mock()
        with patch(
            "asyncio.create_subprocess_exec", return_value=FakeProcess()
        ) as launch:
            asyncio.run(apply_wakamatsu_live_prediction(target, complete, logger))
            asyncio.run(
                apply_wakamatsu_live_prediction(
                    target,
                    {"items": {"direct": complete["items"]["direct"]}},
                    logger,
                )
            )
            asyncio.run(
                apply_wakamatsu_live_prediction(
                    {**target, "venue": "fukuoka"}, complete, logger
                )
            )

        launch.assert_called_once()
        command = launch.call_args.args
        self.assertTrue(command[1].endswith("run_wakamatsu_v2_3_live.py"))
        self.assertEqual(command[2:4], ("--date", "2026-08-25"))
        self.assertNotIn("odds", command)

    def test_v3_staging_hook_runs_after_existing_live_runner_only_when_enabled(self):
        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b'{"status": "complete"}', b""

        target = {"venue": "wakamatsu", "date": "2026-10-15", "race_no": 1}
        complete = {
            "items": {
                key: {"complete": True, "status": "complete"}
                for key in ("direct", "exhibition", "original_exhibition")
            }
        }
        logger = unittest.mock.Mock()
        with patch.dict(os.environ, {"WAKAMATSU_V3_STAGING_OUTPUT_ROOT": "staging/data"}), patch(
            "asyncio.create_subprocess_exec", side_effect=[FakeProcess(), FakeProcess()]
        ) as launch:
            asyncio.run(apply_wakamatsu_live_prediction(target, complete, logger))

        self.assertEqual(launch.call_count, 2)
        existing_command = launch.call_args_list[0].args
        staging_command = launch.call_args_list[1].args
        self.assertTrue(existing_command[1].endswith("run_wakamatsu_v2_3_live.py"))
        self.assertTrue(staging_command[1].endswith("run_wakamatsu_v3_staging.py"))
        self.assertEqual(staging_command[-2:], ("--output-root", "staging/data"))
        self.assertNotIn("odds", staging_command)

    def test_complete_live_race_requires_direct_and_exhibition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            race_root = root / "live" / "2026-08-11" / "wakamatsu" / "05"
            complete = {"complete": True, "status": "complete", "data": {}}
            write_json(race_root / "direct.json", complete)
            self.assertEqual(runner.complete_live_races(root, "2026-08-11"), [])
            write_json(race_root / "exhibition.json", complete)
            self.assertEqual(runner.complete_live_races(root, "2026-08-11"), [5])

    def test_validator_requires_final_and_preserves_pre(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pre = prediction("pre")
            final = prediction("final")
            payload = {
                "engine": runner.ENGINE_ID,
                "engineVersion": runner.ENGINE_VERSION,
                "races": [
                    {
                        "race": 5,
                        "predictionPre": pre,
                        "predictionFinal": final,
                        "prediction": final,
                    }
                ],
            }
            write_json(root / "venues" / "wakamatsu" / "20260811.json", payload)
            report = runner.validate_published_data(root, "2026-08-11", [5])
            self.assertEqual(report["races"][0]["activePredictionPhase"], "final")
            self.assertEqual(
                report["races"][0]["predictionStage"]["label"],
                "本予想",
            )
            self.assertEqual(len(report["races"][0]["tickets"]), 10)

    def test_v23_live_publication_preserves_normalized_fly_probabilities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rates_by_finish = {
                "win": [35.0, 22.0, 16.0, 11.0, 9.0, 7.0],
                "second": [24.0, 23.0, 19.0, 14.0, 11.0, 9.0],
                "third": [16.0, 18.0, 20.0, 19.0, 15.0, 12.0],
            }

            def v23_prediction(phase: str) -> dict:
                value = prediction(phase)
                for finish, rates_for_finish in rates_by_finish.items():
                    value[finish] = {
                        str(lane): rate for lane, rate in enumerate(rates_for_finish, 1)
                    }
                value["engine"] = runner23.ENGINE_ID
                value["engineVersion"] = runner23.ENGINE_VERSION
                if phase == "final":
                    value["predictionStage"] = runner23.FINAL_PREDICTION_STAGE
                build_fly_prediction(
                    value, "live" if phase == "final" else "morning", "general"
                )
                return value

            pre = v23_prediction("pre")
            final = v23_prediction("final")
            payload = {
                "engine": runner23.ENGINE_ID,
                "engineVersion": runner23.ENGINE_VERSION,
                "races": [{
                    "race": 5,
                    "predictionPre": pre,
                    "predictionFinal": final,
                    "prediction": final,
                }],
            }
            dated = root / "venues" / "wakamatsu" / "20260811.json"
            latest = root / "venues" / "wakamatsu" / "latest.json"
            write_json(dated, payload)
            write_json(latest, payload)

            report = runner23.validate_published_data(root, "2026-08-11", [5])
            race_report = report["races"][0]
            self.assertAlmostEqual(
                race_report["morningFlyPrediction"]["normalizationProbabilityTotal"],
                100.0,
                places=10,
            )
            self.assertAlmostEqual(
                race_report["liveFlyPrediction"]["normalizationProbabilityTotal"],
                100.0,
                places=10,
            )
            self.assertEqual(len(race_report["liveFlyPrediction"]["probabilities"]), 10)

            mismatched_latest = json.loads(json.dumps(payload))
            mismatched_latest["races"][0]["predictionFinal"]["flyPrediction"][
                "tickets"
            ][0].pop("probability")
            write_json(latest, mismatched_latest)
            with self.assertRaisesRegex(RuntimeError, "wakamatsu_dated_latest_mismatch"):
                runner23.validate_published_data(root, "2026-08-11", [5])
            write_json(latest, payload)

            payload["races"][0]["predictionFinal"]["flyPrediction"]["tickets"][0].pop(
                "probability"
            )
            payload["races"][0]["prediction"] = payload["races"][0]["predictionFinal"]
            write_json(dated, payload)
            write_json(latest, payload)
            with self.assertRaisesRegex(RuntimeError, "wakamatsu_fly_probability_invalid"):
                runner23.validate_published_data(root, "2026-08-11", [5])

    def test_v23_accepts_provisional_and_legacy_morning_sources_only(self):
        rates_by_finish = {
            "win": [35.0, 22.0, 16.0, 11.0, 9.0, 7.0],
            "second": [24.0, 23.0, 19.0, 14.0, 11.0, 9.0],
            "third": [16.0, 18.0, 20.0, 19.0, 15.0, 12.0],
        }
        for source in ("morning_provisional", "morning"):
            with self.subTest(source=source):
                value = prediction("pre")
                value.update({
                    finish: {str(lane): rate for lane, rate in enumerate(rates, 1)}
                    for finish, rates in rates_by_finish.items()
                })
                build_fly_prediction(value, source, "general")
                report = runner23.validate_fly_prediction(value, "morning", 1)
                self.assertEqual(report["source"], source)

        invalid = prediction("pre")
        invalid.update({
            finish: {str(lane): rate for lane, rate in enumerate(rates, 1)}
            for finish, rates in rates_by_finish.items()
        })
        build_fly_prediction(invalid, "unexpected", "general")
        with self.assertRaisesRegex(RuntimeError, "wakamatsu_fly_schema_invalid"):
            runner23.validate_fly_prediction(invalid, "morning", 1)

    def test_pipeline_runs_apply_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / "venues" / "wakamatsu" / "20260811.json", {})
            complete = {"complete": True, "status": "complete", "data": {}}
            race_root = root / "live" / "2026-08-11" / "wakamatsu" / "05"
            write_json(race_root / "direct.json", complete)
            write_json(race_root / "exhibition.json", complete)
            write_json(race_root / "original_exhibition.json", complete)

            with patch.object(runner.subprocess, "run") as run, patch.object(
                runner, "validate_published_data", return_value={"ok": True}
            ):
                report = runner.run_pipeline("2026-08-11", root, "python")

            self.assertEqual(report, {"ok": True})
            self.assertEqual(run.call_count, 2)
            self.assertTrue(run.call_args_list[0].args[0][1].endswith("apply_wakamatsu_v2.py"))
            self.assertTrue(run.call_args_list[1].args[0][1].endswith("build_site_data.py"))
            self.assertIn("--live-venue", run.call_args_list[1].args[0])

    def test_pipeline_succeeds_when_no_live_races_are_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / "venues" / "wakamatsu" / "20260811.json", {})
            race_root = root / "live" / "2026-08-11" / "wakamatsu" / "05"
            write_json(
                race_root / "direct.json",
                {"complete": True, "status": "complete", "data": {}},
            )
            write_json(
                race_root / "exhibition.json",
                {"complete": False, "status": "incomplete", "data": {}},
            )

            with patch.object(runner.subprocess, "run") as run:
                report = runner.run_pipeline("2026-08-11", root, "python")

            self.assertEqual(
                report,
                {
                    "date": "2026-08-11",
                    "venue": "wakamatsu",
                    "status": "no_complete_live_races",
                    "completeLiveRaces": [],
                    "races": [],
                },
            )
            run.assert_not_called()

    def test_v23_pipeline_v3_failure_fails_closed_without_stopping_existing_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            complete = {"complete": True, "status": "complete", "data": {}}
            race_root = root / "live" / "2026-08-11" / "wakamatsu" / "05"
            write_json(race_root / "direct.json", complete)
            write_json(race_root / "exhibition.json", complete)
            write_json(race_root / "original_exhibition.json", complete)
            write_json(root / "venues" / "wakamatsu" / "20260811.json", {})
            fallback = {"status": "unavailable", "reason": "v3_pipeline_error:RuntimeError"}
            with patch.object(runner23.subprocess, "run"), patch.object(
                runner23, "validate_published_data", return_value={"ok": True}
            ), patch.object(
                runner23, "publish_v3", side_effect=RuntimeError("test")
            ), patch.object(
                runner23, "mark_live_v3_unavailable", return_value=fallback
            ) as fail_closed:
                report = runner23.run_pipeline("2026-08-11", root, "python")
            self.assertTrue(report["ok"])
            self.assertEqual(report["wakamatsuV3"], fallback)
            fail_closed.assert_called_once_with(
                "2026-08-11", root.resolve(), "v3_pipeline_error:RuntimeError"
            )


if __name__ == "__main__":
    unittest.main()
