from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import run_wakamatsu_v2_live as runner


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prediction(phase: str) -> dict:
    return {
        "phase": phase,
        "finalPredictionStatus": "complete" if phase == "final" else "waiting_live_data",
        "engine": runner.ENGINE_ID,
        "engineVersion": runner.ENGINE_VERSION,
        "win": {str(lane): float(lane) for lane in range(1, 7)},
        "second": {str(lane): float(lane + 1) for lane in range(1, 7)},
        "third": {str(lane): float(lane + 2) for lane in range(1, 7)},
        "sab": "A",
        "tickets": [{"combo": f"1-2-{lane}"} for lane in range(1, 7)]
        + [{"combo": f"2-1-{lane}"} for lane in range(1, 5)],
        "diagnostics": {"oddsUsedForPrediction": False},
    }


class WakamatsuLiveRunnerTest(unittest.TestCase):
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
            self.assertEqual(len(report["races"][0]["tickets"]), 10)

    def test_pipeline_runs_apply_before_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(root / "venues" / "wakamatsu" / "20260811.json", {})
            complete = {"complete": True, "status": "complete", "data": {}}
            race_root = root / "live" / "2026-08-11" / "wakamatsu" / "05"
            write_json(race_root / "direct.json", complete)
            write_json(race_root / "exhibition.json", complete)

            with patch.object(runner.subprocess, "run") as run, patch.object(
                runner, "validate_published_data", return_value={"ok": True}
            ):
                report = runner.run_pipeline("2026-08-11", root, "python")

            self.assertEqual(report, {"ok": True})
            self.assertEqual(run.call_count, 2)
            self.assertTrue(run.call_args_list[0].args[0][1].endswith("apply_wakamatsu_v2.py"))
            self.assertTrue(run.call_args_list[1].args[0][1].endswith("build_site_data.py"))
            self.assertIn("--live-venue", run.call_args_list[1].args[0])


if __name__ == "__main__":
    unittest.main()
