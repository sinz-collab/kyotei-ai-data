from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import live_fetch_once  # noqa: E402
from live_common import process_lock  # noqa: E402


class TokonameIndependentReleaseTests(unittest.TestCase):
    def test_live_parent_uses_fresh_worker_process(self):
        config = {"morning_data_root": "runtime/morning"}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"status": "updated"}), stderr=""
        )
        with patch.object(live_fetch_once.subprocess, "run", return_value=completed) as run:
            report = live_fetch_once.run_tokoname_prediction_worker(
                config,
                "2026-11-26",
                ROOT / "data" / "live",
                ROOT / "runtime" / "predictions",
                race_numbers=[3],
            )
        self.assertEqual(report["status"], "updated")
        command = run.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertIn("tokoname_prediction_worker.py", command[1])
        self.assertEqual(command[command.index("--races") + 1], "3")
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONDONTWRITEBYTECODE"], "1")

    def test_busy_worker_is_a_safe_non_prediction_result(self):
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=75,
            stdout=json.dumps({"status": "busy", "reason": "tokoname_engine_lock_busy"}),
            stderr="",
        )
        with patch.object(live_fetch_once.subprocess, "run", return_value=completed):
            report = live_fetch_once.run_tokoname_prediction_worker(
                {"morning_data_root": "runtime/morning"},
                "2026-11-26",
                ROOT / "data" / "live",
                ROOT / "runtime" / "predictions",
            )
        self.assertEqual(report, {"status": "busy", "reason": "tokoname_engine_lock_busy"})

    def test_worker_self_check_loads_engine_normal_and_fly_components(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "tokoname_prediction_worker.py"),
                    "--self-check",
                    "--lock-path",
                    str(Path(directory) / "engine.lock"),
                ],
                cwd=ROOT,
                env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["engine"], "tokoname_engine")
        self.assertEqual(payload["normalModelsLoaded"], ["top2", "top3", "win"])
        self.assertEqual(payload["flyTicketsVersion"], "tokoname_fly_tickets_v4")
        self.assertEqual(payload["flyModelsLoaded"], 3)
        self.assertTrue(payload["componentHashes"])

    def test_prediction_worker_rejects_when_engine_lock_is_held(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "engine.lock"
            if sys.platform == "win32":
                lock_path.write_text("0", encoding="ascii")
            with process_lock(lock_path) as acquired:
                self.assertTrue(acquired)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "tokoname_prediction_worker.py"),
                        "--self-check",
                        "--lock-path",
                        str(lock_path),
                    ],
                    cwd=ROOT,
                    env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=False,
                    timeout=30,
                )
        self.assertEqual(result.returncode, 75, result.stderr)
        self.assertEqual(json.loads(result.stdout)["reason"], "tokoname_engine_lock_busy")

    def test_release_script_keeps_main_pid_and_only_locks_tokoname(self):
        script = (ROOT / "deploy" / "engine-release" / "sinz-engine-apply").read_text(
            encoding="utf-8"
        )
        self.assertIn('for name in tokoname tokoname-engine', script)
        self.assertIn('main_pid_after == "$main_pid_before"', script)
        self.assertNotIn('systemctl restart "$service"', script)
        self.assertNotIn("venue_locks=(", script)


if __name__ == "__main__":
    unittest.main()
