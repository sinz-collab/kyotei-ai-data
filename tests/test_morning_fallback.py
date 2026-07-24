from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from check_morning_manifest import manifest_is_current, run_check


JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 7, 25, 6, 40, tzinfo=JST)


def manifest(date: str = "2026-07-25", updated_at: str | None = None) -> dict:
    return {
        "date": date,
        "updatedAt": updated_at or f"{date}T06:35:00+09:00",
        "venues": [],
    }


def run(
    run_id: int,
    status: str,
    conclusion: str | None = None,
    event: str = "schedule",
) -> dict:
    return {
        "id": run_id,
        "status": status,
        "conclusion": conclusion,
        "event": event,
        "created_at": "2026-07-24T21:35:00Z",
    }


class FakeGitHub:
    def __init__(
        self,
        runs: list[dict] | None = None,
        *,
        list_error: Exception | None = None,
        dispatch_error: Exception | None = None,
    ) -> None:
        self.runs = runs or []
        self.list_error = list_error
        self.dispatch_error = dispatch_error
        self.dispatches: list[str] = []

    def list_runs(self) -> list[dict]:
        if self.list_error:
            raise self.list_error
        return self.runs

    def dispatch(self, target_date: str) -> None:
        self.dispatches.append(target_date)
        if self.dispatch_error:
            raise self.dispatch_error

    def find_dispatched_run(self, target_date: str, not_before: datetime) -> dict:
        return {
            "id": 9001,
            "event": "workflow_dispatch",
            "status": "queued",
            "conclusion": None,
            "created_at": "2026-07-24T21:40:01Z",
        }

    def get_run(self, run_id: int) -> dict:
        for item in self.runs:
            if item["id"] == run_id:
                return item
        return {"id": run_id, "status": "queued", "conclusion": None}


class MorningFallbackTests(unittest.TestCase):
    def invoke(
        self,
        root: Path,
        github: FakeGitHub,
        loaded_manifest: dict | Exception,
        *,
        mode: str = "fallback",
        enabled: bool = True,
        dry_run: bool = False,
        now: datetime = NOW,
    ) -> tuple[dict, list[dict]]:
        logs: list[dict] = []

        def loader() -> dict:
            if isinstance(loaded_manifest, Exception):
                raise loaded_manifest
            return loaded_manifest

        def logger(event: str, **fields: object) -> None:
            logs.append({"event": event, **fields})

        result = run_check(
            mode=mode,
            now=now,
            manifest_loader=loader,
            github=github,
            state_dir=root,
            dispatch_enabled=enabled,
            dry_run=dry_run,
            log=logger,
        )
        return result, logs

    def test_current_manifest_does_not_call_github_or_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = Mock()
            result, _ = self.invoke(Path(temporary), github, manifest())
        self.assertEqual(result["reason"], "manifest_current")
        github.list_runs.assert_not_called()
        github.dispatch.assert_not_called()

    def test_previous_manifest_and_no_run_dispatches_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            github = FakeGitHub()
            result, _ = self.invoke(root, github, manifest("2026-07-24"))
            state = json.loads((root / "2026-07-25.json").read_text())
        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(github.dispatches, ["2026-07-25"])
        self.assertEqual(state["runId"], 9001)
        self.assertEqual(state["outcome"], "run_observed")

    def test_queued_run_blocks_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub([run(10, "queued")])
            result, _ = self.invoke(
                Path(temporary), github, manifest("2026-07-24")
            )
        self.assertEqual(result["reason"], "workflow_active")
        self.assertEqual(github.dispatches, [])

    def test_in_progress_run_blocks_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub([run(11, "in_progress")])
            result, _ = self.invoke(
                Path(temporary), github, manifest("2026-07-24")
            )
        self.assertEqual(result["reason"], "workflow_active")
        self.assertEqual(github.dispatches, [])

    def test_same_day_success_blocks_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub([run(12, "completed", "success")])
            result, _ = self.invoke(
                Path(temporary), github, manifest("2026-07-24")
            )
        self.assertEqual(result["reason"], "workflow_already_succeeded")
        self.assertEqual(github.dispatches, [])

    def test_failed_run_allows_one_fallback_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub([run(13, "completed", "failure")])
            result, _ = self.invoke(
                Path(temporary), github, manifest("2026-07-24")
            )
        self.assertEqual(result["action"], "dispatched")
        self.assertEqual(github.dispatches, ["2026-07-25"])

    def test_second_same_day_dispatch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            github = FakeGitHub()
            first, _ = self.invoke(root, github, manifest("2026-07-24"))
            second, _ = self.invoke(root, github, manifest("2026-07-24"))
        self.assertEqual(first["action"], "dispatched")
        self.assertEqual(second["reason"], "daily_dispatch_already_attempted")
        self.assertEqual(github.dispatches, ["2026-07-25"])

    def test_github_api_failure_never_dispatches_or_changes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            existing = data / "manifest.json"
            existing.write_text(json.dumps(manifest("2026-07-24")))
            before = existing.read_bytes()
            github = FakeGitHub(list_error=RuntimeError("api unavailable"))
            result, _ = self.invoke(
                root / "state",
                github,
                json.loads(existing.read_text()),
            )
            after = existing.read_bytes()
        self.assertEqual(result["reason"], "github_api_error")
        self.assertEqual(before, after)
        self.assertEqual(github.dispatches, [])

    def test_secret_value_is_never_logged(self) -> None:
        secret = "github_pat_NEVER_LOG_THIS"
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub(list_error=RuntimeError(secret))
            _, logs = self.invoke(
                Path(temporary), github, manifest("2026-07-24")
            )
        self.assertNotIn(secret, json.dumps(logs))
        self.assertIn("RuntimeError", json.dumps(logs))

    def test_monitor_does_not_modify_live_or_prediction_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            live = root / "data" / "live" / "2026-07-24" / "toda" / "01"
            venue = root / "data" / "venues" / "toda"
            live.mkdir(parents=True)
            venue.mkdir(parents=True)
            live_file = live / "odds.json"
            prediction_file = venue / "20260724.json"
            live_file.write_text('{"odds":{"1-2-3":10.0}}')
            prediction_file.write_text('{"preds":{"1":{"ai":["1-2-3"]}}}')
            before = (live_file.read_bytes(), prediction_file.read_bytes())
            github = FakeGitHub()
            self.invoke(root / "state", github, manifest("2026-07-24"))
            after = (live_file.read_bytes(), prediction_file.read_bytes())
        self.assertEqual(before, after)

    def test_verify_mode_never_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub()
            result, _ = self.invoke(
                Path(temporary),
                github,
                manifest("2026-07-24"),
                mode="verify",
                now=datetime(2026, 7, 25, 6, 55, tzinfo=JST),
            )
        self.assertEqual(result["reason"], "verify_only")
        self.assertEqual(github.dispatches, [])

    def test_dispatch_failure_is_recorded_and_not_retried(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            github = FakeGitHub(dispatch_error=RuntimeError("denied"))
            first, _ = self.invoke(root, github, manifest("2026-07-24"))
            second, _ = self.invoke(root, github, manifest("2026-07-24"))
            state = json.loads((root / "2026-07-25.json").read_text())
        self.assertEqual(first["reason"], "dispatch_error")
        self.assertEqual(second["reason"], "daily_dispatch_already_attempted")
        self.assertEqual(len(github.dispatches), 1)
        self.assertEqual(state["outcome"], "dispatch_error")

    def test_disabled_and_dry_run_do_not_create_state(self) -> None:
        for enabled, dry_run, expected in (
            (False, False, "dispatch_disabled"),
            (True, True, "dry_run"),
        ):
            with self.subTest(enabled=enabled, dry_run=dry_run):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    github = FakeGitHub()
                    result, _ = self.invoke(
                        root,
                        github,
                        manifest("2026-07-24"),
                        enabled=enabled,
                        dry_run=dry_run,
                    )
                    self.assertEqual(result["reason"], expected)
                    self.assertFalse((root / "2026-07-25.json").exists())
                    self.assertEqual(github.dispatches, [])

    def test_outside_window_never_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            github = FakeGitHub()
            result, _ = self.invoke(
                Path(temporary),
                github,
                manifest("2026-07-24"),
                now=datetime(2026, 7, 25, 7, 0, tzinfo=JST),
            )
        self.assertEqual(result["reason"], "outside_dispatch_window")
        self.assertEqual(github.dispatches, [])

    def test_updated_at_must_be_same_jst_date(self) -> None:
        current, date, updated = manifest_is_current(
            manifest("2026-07-25", "2026-07-24T23:50:00+09:00"),
            "2026-07-25",
        )
        self.assertFalse(current)
        self.assertEqual(date, "2026-07-25")
        self.assertEqual(updated, "2026-07-24T23:50:00+09:00")

    def test_production_urls_do_not_reference_llm_services(self) -> None:
        source = (
            (SCRIPTS / "check_morning_manifest.py").read_text()
            + (SCRIPTS / "trigger_morning_workflow.py").read_text()
        ).lower()
        for forbidden in ("api.openai.com", "anthropic.com", "generativelanguage"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
