from __future__ import annotations

import sys
import json
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest.mock import patch


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

import fetch_one  # noqa: E402


class FetchOnePrecheckTest(unittest.TestCase):
    def test_uses_official_racelist_for_scheduled_venue(self) -> None:
        html = "<html>出走表 ボートレーサー モーター</html>"
        with patch.object(fetch_one, "request_text", return_value=("official", html)) as request:
            result = fetch_one.venue_is_open("tokoname", "2026-11-15")

        self.assertTrue(result["open"])
        self.assertEqual(result["reason"], "race_page_found")
        self.assertEqual(result["source"], "BOAT RACE official")
        request.assert_called_once_with(
            "https://www.boatrace.jp/owpc/pc/race/racelist?rno=1&jcd=08&hd=20261115"
        )

    def test_official_page_without_race_markers_is_not_scheduled(self) -> None:
        with patch.object(fetch_one, "request_text", return_value=("official", "開催一覧")):
            result = fetch_one.venue_is_open("kiryu", "2026-11-15")

        self.assertFalse(result["open"])
        self.assertEqual(result["reason"], "not_scheduled")

    def test_http_403_is_recorded_as_fetch_failure(self) -> None:
        error = urllib.error.HTTPError("https://example.invalid", 403, "Forbidden", {}, None)
        with (
            patch.object(fetch_one.urllib.request, "urlopen", side_effect=error),
            patch.object(fetch_one.time, "sleep"),
        ):
            with self.assertRaises(fetch_one.RequestFailed) as caught:
                fetch_one.request_text("https://example.invalid", attempts=2)

        self.assertEqual(caught.exception.http_status, 403)

        with patch.object(
            fetch_one,
            "request_text",
            side_effect=fetch_one.RequestFailed(["HTTP Error 403: Forbidden"], http_status=403),
        ):
            result = fetch_one.venue_is_open("omura", "2026-11-15")

        self.assertFalse(result["open"])
        self.assertEqual(result["reason"], "precheck_failed:http_403")
        self.assertEqual(result["httpStatus"], 403)
        self.assertIn("403", result["error"])

    def test_main_writes_403_as_failure_not_non_scheduled(self) -> None:
        precheck = {
            "open": False,
            "reason": "precheck_failed:http_403",
            "source": "BOAT RACE official",
            "httpStatus": 403,
            "error": "HTTP Error 403: Forbidden",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(fetch_one, "load_venue", return_value={"slug": "omura", "name": "大村"}),
                patch.object(fetch_one, "venue_is_open", return_value=precheck),
                patch.object(
                    sys,
                    "argv",
                    ["fetch_one.py", "--venue", "omura", "--date", "2026-11-15", "--root", str(root)],
                ),
            ):
                self.assertEqual(fetch_one.main(), 1)

            status_path = root / "大村" / "20261115" / "fetch_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertFalse(status["open"])
            self.assertEqual(status["precheck"]["reason"], "precheck_failed:http_403")
            self.assertEqual(status["precheck"]["httpStatus"], 403)

    def test_hard_empty_fetch_is_not_repeated_immediately(self) -> None:
        precheck = {"open": True, "reason": "race_page_found"}
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with (
                patch.object(fetch_one, "load_venue", return_value={"slug": "tokoname", "name": "常滑"}),
                patch.object(fetch_one, "venue_is_open", return_value=precheck),
                patch.object(fetch_one.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()) as run,
                patch.object(fetch_one, "fetch_tide", return_value={"status": "not_configured"}),
                patch.object(sys, "argv", [
                    "fetch_one.py", "--venue", "tokoname", "--date", "2026-11-17",
                    "--root", str(root),
                ]),
            ):
                self.assertEqual(fetch_one.main(), 1)
            run.assert_called_once()
            status = json.loads(
                (root / "常滑" / "20261117" / "fetch_status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(status["fetchAttempts"]), 1)
            self.assertEqual(status["fetchAttempts"][0]["error"], "fetch_process_exit_1")


if __name__ == "__main__":
    unittest.main()
