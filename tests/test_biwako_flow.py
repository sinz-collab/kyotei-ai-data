from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_site_data

# The publish job intentionally installs only the lightweight site-build
# dependencies.  These tests exercise the venue mapping helpers, not a browser,
# so provide the minimum import surface when Playwright is absent there.
try:
    import boaters_fetch
except ModuleNotFoundError as exc:
    if exc.name not in {"playwright", "playwright.async_api"}:
        raise
    playwright_stub = types.ModuleType("playwright")
    playwright_async_stub = types.ModuleType("playwright.async_api")
    playwright_async_stub.async_playwright = lambda: None
    playwright_async_stub.TimeoutError = TimeoutError
    sys.modules["playwright"] = playwright_stub
    sys.modules["playwright.async_api"] = playwright_async_stub
    import boaters_fetch
import fetch_one
from detect_active_venues import detect_active_venues
from publish_live_data import copy_changed_live_files
from select_target_races import select_target_races


DATE = "2026-08-17"
COMPACT_DATE = "20260817"


def race_payload() -> dict:
    races = []
    for race_no in range(1, 13):
        racers = [
            {"lane": lane, "name": f"Racer {lane}", "season_runs": [], "season_groups": []}
            for lane in range(1, 7)
        ]
        races.append(
            {
                "race": race_no,
                "deadline": f"{9 + race_no // 4:02d}:{(race_no * 5) % 60:02d}",
                "racers": racers,
                "type": "一般",
            }
        )
    return {
        "venueId": "biwako",
        "venue": "びわこ",
        "date": DATE,
        "engine": "",
        "preds": {},
        "races": races,
        "tide": {},
    }


class BiwakoFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        config = json.loads((ROOT / "automation" / "venues.json").read_text(encoding="utf-8"))
        self.venue = next(item for item in config["venues"] if item["slug"] == "biwako")

    def test_configuration_has_no_tide_and_has_prediction_engine(self) -> None:
        self.assertEqual(self.venue, {"slug": "biwako", "name": "びわこ"})
        self.assertIn("biwako", build_site_data.PREDICTION_VENUES)
        self.assertEqual(boaters_fetch.normalize_stadium("biwako"), "びわこ")
        self.assertEqual(boaters_fetch.stadium_to_slug("びわこ"), "biwako")
        workflow = (ROOT / ".github" / "workflows" / "morning-data.yml").read_text(encoding="utf-8")
        self.assertIn("heiwajima biwako", workflow)
        self.assertIn(
            "work/races/びわこ/**/races/*_boaters_local_st.json",
            workflow,
        )
        self.assertNotIn(
            "work/races/**/races/*_boaters_local_st.json",
            workflow,
        )

    def test_biwako_entry_count_requires_six_parseable_racers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            races_dir = output_dir / "races"
            races_dir.mkdir()
            for race_no in range(1, 13):
                (races_dir / f"race_{race_no:02d}_entry.txt").write_text(
                    "entry data\n" * 100,
                    encoding="utf-8",
                )

            def valid_except_race_9(path: Path) -> bool:
                return path.name != "race_09_entry.txt"

            with patch.object(
                fetch_one,
                "biwako_entry_is_valid",
                side_effect=valid_except_race_9,
            ):
                self.assertEqual(fetch_one.count_entries(output_dir, "biwako"), 11)

            with patch.object(fetch_one, "biwako_entry_is_valid", return_value=True):
                self.assertEqual(fetch_one.count_entries(output_dir, "biwako"), 12)

    def test_other_venues_keep_size_only_entry_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            races_dir = output_dir / "races"
            races_dir.mkdir()
            for race_no in range(1, 13):
                size = 501 if race_no != 9 else 500
                (races_dir / f"race_{race_no:02d}_entry.txt").write_text(
                    "x" * size,
                    encoding="utf-8",
                )
            with patch.object(fetch_one, "biwako_entry_is_valid") as validator:
                self.assertEqual(fetch_one.count_entries(output_dir, "ashiya"), 11)
            validator.assert_not_called()

    def test_biwako_incomplete_fetch_uses_only_existing_two_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "venues.json"
            config_path.write_text(
                json.dumps({"venues": [self.venue]}, ensure_ascii=False),
                encoding="utf-8",
            )
            completed = Mock(returncode=0)
            with (
                patch.object(fetch_one, "CONFIG_PATH", config_path),
                patch.object(
                    fetch_one,
                    "venue_is_open",
                    return_value={"open": True, "reason": "race_page_found"},
                ),
                patch.object(fetch_one.subprocess, "run", return_value=completed) as runner,
                patch.object(fetch_one, "count_entries", side_effect=[11, 11]),
                patch.object(fetch_one, "fetch_tide", return_value={"status": "not_configured"}),
                patch.object(fetch_one.time, "sleep"),
                patch.object(
                    sys,
                    "argv",
                    [
                        "fetch_one.py",
                        "--venue",
                        "biwako",
                        "--date",
                        DATE,
                        "--root",
                        str(root / "work"),
                    ],
                ),
            ):
                self.assertEqual(fetch_one.main(), 1)
            self.assertEqual(runner.call_count, 2)

    def test_biwako_player_ids_are_read_from_entry_html(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            html = Path(temporary) / "race_01_entry.html"
            html.write_text(
                '"boatNumber":1,"regN":3606,"name":"Racer 1"'
                '"boatNumber":6,"regN":5460,"name":"Racer 6"',
                encoding="utf-8",
            )
            self.assertEqual(
                build_site_data.parse_biwako_player_ids(html),
                {1: 3606, 6: 5460},
            )

    def run_build(self, root: Path, fetch_status: dict, payload: dict | None) -> tuple[dict, Path]:
        source_root = root / "source"
        data_root = root / "data"
        source_dir = source_root / "びわこ" / COMPACT_DATE
        source_dir.mkdir(parents=True)
        (source_dir / "fetch_status.json").write_text(
            json.dumps(fetch_status, ensure_ascii=False), encoding="utf-8"
        )
        config_path = root / "venues.json"
        config_path.write_text(
            json.dumps({"venues": [self.venue]}, ensure_ascii=False), encoding="utf-8"
        )
        with (
            patch.object(build_site_data, "CONFIG_PATH", config_path),
            patch.object(build_site_data, "ALL_VENUES", [("biwako", "びわこ")]),
            patch.object(build_site_data, "build_payload", return_value=(payload, {"reason": "ok"})),
            patch.object(build_site_data, "annotate_no_prior_meeting_runs", side_effect=lambda value, *_: value),
            patch.object(
                sys,
                "argv",
                [
                    "build_site_data.py",
                    "--date",
                    DATE,
                    "--source-root",
                    str(source_root),
                    "--data-root",
                    str(data_root),
                ],
            ),
        ):
            result = build_site_data.main()
        manifest = json.loads((data_root / "manifest.json").read_text(encoding="utf-8"))
        return manifest, data_root

    def test_running_meeting_publishes_races_without_predictions_or_tide(self) -> None:
        status = {
            "slug": "biwako",
            "name": "びわこ",
            "date": DATE,
            "open": True,
            "entryCount": 12,
            "precheck": {"open": True, "reason": "race_page_found"},
            "tide": {"status": "not_configured"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            manifest, data_root = self.run_build(Path(temporary), status, race_payload())
            venue = manifest["venues"][0]
            self.assertTrue(venue["open"])
            self.assertEqual(venue["predictionStatus"], "unavailable")
            self.assertEqual(venue["predictionReason"], "prediction_payload_unavailable")
            document = json.loads((data_root / venue["dataPath"]).read_text(encoding="utf-8"))
            self.assertEqual(len(document["races"]), 12)
            self.assertEqual(document["engine"], "")
            self.assertEqual(document["preds"], {})
            self.assertEqual(document["tide"], {})

            active = detect_active_venues(data_root / "manifest.json", DATE)
            self.assertEqual([item["slug"] for item in active], ["biwako"])

    def test_non_running_meeting_creates_no_public_venue_json(self) -> None:
        status = {
            "slug": "biwako",
            "name": "びわこ",
            "date": DATE,
            "open": False,
            "entryCount": 0,
            "precheck": {"open": False, "reason": "not_scheduled"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            manifest, data_root = self.run_build(Path(temporary), status, None)
            self.assertFalse(manifest["venues"][0]["open"])
            self.assertFalse((data_root / "venues" / "biwako" / f"{COMPACT_DATE}.json").exists())

    def test_running_meeting_failure_stops_site_build(self) -> None:
        status = {
            "slug": "biwako",
            "name": "びわこ",
            "date": DATE,
            "open": False,
            "entryCount": 9,
            "fetchReturnCode": 1,
            "precheck": {"open": True, "reason": "race_page_found"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(RuntimeError, "scheduled meeting fetch incomplete"):
                self.run_build(Path(temporary), status, None)

    def test_precheck_error_is_failure_but_non_running_is_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "venues.json"
            config_path.write_text(json.dumps({"venues": [self.venue]}, ensure_ascii=False), encoding="utf-8")
            with (
                patch.object(fetch_one, "CONFIG_PATH", config_path),
                patch.object(fetch_one, "venue_is_open", return_value={"open": False, "reason": "not_scheduled"}),
                patch.object(sys, "argv", ["fetch_one.py", "--venue", "biwako", "--date", DATE, "--root", str(root / "work")]),
            ):
                self.assertEqual(fetch_one.main(), 0)
            with (
                patch.object(fetch_one, "CONFIG_PATH", config_path),
                patch.object(fetch_one, "venue_is_open", return_value={"open": False, "reason": "precheck_failed:TimeoutError"}),
                patch.object(sys, "argv", ["fetch_one.py", "--venue", "biwako", "--date", DATE, "--root", str(root / "work2")]),
            ):
                self.assertEqual(fetch_one.main(), 1)

    def test_publisher_copies_biwako_live_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "live"
            document = source / DATE / "biwako" / "01" / "direct.json"
            document.parent.mkdir(parents=True)
            document.write_text("{}\n", encoding="utf-8")
            repository = root / "publisher"
            self.assertEqual(copy_changed_live_files(source, repository), 1)
            self.assertTrue((repository / "data" / "live" / DATE / "biwako" / "01" / "direct.json").is_file())

    def test_live_monitor_selects_biwako_before_deadline(self) -> None:
        venue = {
            "slug": "biwako",
            "name": "びわこ",
            "payload": race_payload(),
        }
        config = {
            "timezone": "Asia/Tokyo",
            "race_monitor_minutes_before_deadline": 45,
            "result_monitor_minutes_after_deadline": 900,
        }
        now = datetime(2026, 8, 17, 8, 21, tzinfo=ZoneInfo("Asia/Tokyo"))
        targets = select_target_races(venue, now, config, Path("live"))
        self.assertEqual(
            [(item["venue"], item["race_no"], item["fetch_live"]) for item in targets],
            [("biwako", 1, True)],
        )


if __name__ == "__main__":
    unittest.main()
