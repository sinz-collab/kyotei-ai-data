from __future__ import annotations

import itertools
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from detect_active_venues import detect_active_venues
from fetch_live_race import save_document
from fetch_odds import parse_odds
from live_common import atomic_write_json, is_fetch_window, load_config, process_lock
from live_data_hash import content_hash
from select_target_races import select_target_races
from sync_morning_data import ensure_current_morning_data
from validate_live_data import validate_live_data


CONFIG = load_config(Path(__file__).resolve().parents[1] / "config" / "live_fetch_config.json")
JST = ZoneInfo("Asia/Tokyo")


def at(value: str) -> datetime:
    return datetime.fromisoformat(f"2026-07-24T{value}:00+09:00")


def racers() -> list[dict]:
    return [{"lane": lane, "player_id": f"10{lane}"} for lane in range(1, 7)]


class TimeWindowTests(unittest.TestCase):
    def test_boundaries(self) -> None:
        expected = {
            "08:19": False,
            "08:20": True,
            "08:21": True,
            "10:56": True,
            "18:00": True,
            "22:56": True,
            "22:59": True,
            "23:00": False,
            "23:01": False,
        }
        for value, result in expected.items():
            with self.subTest(value=value):
                self.assertEqual(is_fetch_window(at(value), CONFIG), result)

    def test_next_day_boundaries(self) -> None:
        self.assertFalse(is_fetch_window(datetime(2026, 7, 25, 8, 19, tzinfo=JST), CONFIG))
        self.assertTrue(is_fetch_window(datetime(2026, 7, 25, 8, 20, tzinfo=JST), CONFIG))


class VenueAndRaceTests(unittest.TestCase):
    def test_only_complete_open_today(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "venues" / "a").mkdir(parents=True)
            payload = {
                "date": "2026-07-24",
                "races": [
                    {"race": race, "deadline": "09:00", "racers": racers()}
                    for race in range(1, 13)
                ],
            }
            (root / "venues" / "a" / "20260724.json").write_text(json.dumps(payload), encoding="utf-8")
            manifest = {
                "date": "2026-07-24",
                "venues": [
                    {
                        "slug": "a",
                        "name": "A",
                        "open": True,
                        "date": "2026-07-24",
                        "entryCount": 12,
                        "dataPath": "venues/a/20260724.json",
                    },
                    {
                        "slug": "b",
                        "name": "B",
                        "open": False,
                        "date": "2026-07-24",
                        "entryCount": 12,
                        "dataPath": "venues/b/20260724.json",
                    },
                ],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            active = detect_active_venues(manifest_path, "2026-07-24")
            self.assertEqual([venue["slug"] for venue in active], ["a"])
            with self.assertRaises(ValueError):
                detect_active_venues(manifest_path, "2026-07-25")

            payload["races"][0]["deadline"] = ""
            (root / "venues" / "a" / "20260724.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(detect_active_venues(manifest_path, "2026-07-24"), [])

    def test_45_minute_window(self) -> None:
        venue = {
            "slug": "a",
            "name": "A",
            "payload": {
                "date": "2026-07-24",
                "races": [
                    {"race": 1, "deadline": "09:05", "racers": racers()},
                    {"race": 2, "deadline": "09:06", "racers": racers()},
                    {"race": 3, "deadline": "08:20", "racers": racers()},
                    {"race": 4, "deadline": "09:00", "racers": racers(), "cancelled": True},
                ],
            },
        }
        targets = select_target_races(venue, at("08:20"), CONFIG)
        self.assertEqual([target["race_no"] for target in targets], [1])

    def test_current_morning_data_checks_manifest_without_rewriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload_path = root / "venues" / "a" / "20260724.json"
            payload_path.parent.mkdir(parents=True)
            payload = {
                "date": "2026-07-24",
                "races": [
                    {"race": race, "deadline": "09:00", "racers": racers()}
                    for race in range(1, 13)
                ],
            }
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest = {
                "date": "2026-07-24",
                "venues": [
                    {
                        "slug": "a",
                        "open": True,
                        "date": "2026-07-24",
                        "entryCount": 12,
                        "dataPath": "venues/a/20260724.json",
                    }
                ],
            }
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            config = {**CONFIG, "morning_data_root": str(root)}
            with patch("sync_morning_data._request_json", return_value=manifest) as request:
                path = ensure_current_morning_data(config, "2026-07-24", Mock())
            self.assertEqual(path, root / "manifest.json")
            request.assert_called_once()

    def test_newer_same_day_manifest_replaces_valid_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload_path = root / "venues" / "a" / "20260724.json"
            payload_path.parent.mkdir(parents=True)
            payload = {
                "date": "2026-07-24",
                "races": [
                    {"race": race, "deadline": "09:00", "racers": racers()}
                    for race in range(1, 13)
                ],
            }
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            local_manifest = {
                "date": "2026-07-24",
                "updatedAt": "2026-07-24T06:35:00",
                "venues": [
                    {
                        "slug": "a",
                        "open": True,
                        "date": "2026-07-24",
                        "entryCount": 12,
                        "dataPath": "venues/a/20260724.json",
                    }
                ],
            }
            remote_manifest = {
                **local_manifest,
                "updatedAt": "2026-07-24T13:08:35+09:00",
            }
            (root / "manifest.json").write_text(json.dumps(local_manifest), encoding="utf-8")
            config = {**CONFIG, "morning_data_root": str(root)}
            with patch(
                "sync_morning_data._request_json",
                side_effect=[remote_manifest, payload],
            ):
                ensure_current_morning_data(config, "2026-07-24", Mock())
            self.assertEqual(
                json.loads((root / "manifest.json").read_text(encoding="utf-8")),
                remote_manifest,
            )

    def test_refresh_failure_preserves_valid_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload_path = root / "venues" / "a" / "20260724.json"
            payload_path.parent.mkdir(parents=True)
            payload = {
                "date": "2026-07-24",
                "races": [
                    {"race": race, "deadline": "09:00", "racers": racers()}
                    for race in range(1, 13)
                ],
            }
            payload_path.write_text(json.dumps(payload), encoding="utf-8")
            manifest = {
                "date": "2026-07-24",
                "venues": [
                    {
                        "slug": "a",
                        "open": True,
                        "date": "2026-07-24",
                        "entryCount": 12,
                        "dataPath": "venues/a/20260724.json",
                    }
                ],
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            config = {**CONFIG, "morning_data_root": str(root)}
            with patch(
                "sync_morning_data._request_json",
                side_effect=RuntimeError("temporary outage"),
            ):
                path = ensure_current_morning_data(config, "2026-07-24", Mock())
            self.assertEqual(path, manifest_path)
            self.assertEqual(json.loads(manifest_path.read_text()), manifest)

    def test_stale_remote_manifest_preserves_local_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old_manifest = {"date": "2026-07-23", "venues": []}
            manifest_path = root / "manifest.json"
            root.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(old_manifest), encoding="utf-8")
            config = {**CONFIG, "morning_data_root": str(root)}
            with patch(
                "sync_morning_data._request_json",
                return_value={"date": "2026-07-23", "venues": []},
            ):
                with self.assertRaises(RuntimeError):
                    ensure_current_morning_data(config, "2026-07-24", Mock())
            self.assertEqual(json.loads(manifest_path.read_text()), old_manifest)


class OddsHashAndStorageTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "POSIX file modes are required")
    def test_atomic_json_is_group_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "status.json"
            atomic_write_json(path, {"status": "pending"})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_all_120_odds(self) -> None:
        sections = []
        for first in range(1, 7):
            sections.append(f"{first}.")
            for second in range(1, 7):
                if second == first:
                    continue
                sections.append(str(second))
                for third in range(1, 7):
                    if third in {first, second}:
                        continue
                    sections.extend([str(third), "12.3"])
        parsed = parse_odds("\n".join(sections))
        self.assertEqual(parsed["count"], 120)
        self.assertTrue(parsed["_complete"])

    def test_partial_odds(self) -> None:
        parsed = parse_odds("1.\n2\n3\n12.3")
        self.assertEqual(parsed["count"], 1)
        self.assertFalse(parsed["_complete"])

    def test_hash_ignores_fetch_time_and_order(self) -> None:
        left = {"fetched_at": "a", "data": {"entries": [{"lane": 2}, {"lane": 1}]}}
        right = {"fetched_at": "b", "data": {"entries": [{"lane": 1}, {"lane": 2}]}}
        self.assertEqual(content_hash(left), content_hash(right))

    def test_incomplete_does_not_replace_complete(self) -> None:
        target = {
            "date": "2026-07-24",
            "venue": "a",
            "race_no": 1,
            "deadline": "09:00",
            "racers": racers(),
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exhibition.json"
            complete = {
                **{key: target[key] for key in ("date", "venue", "race_no", "deadline")},
                "fetched_at": "2026-07-24T08:30:00+09:00",
                "source": "fixture",
                "status": "complete",
                "complete": True,
                "content_hash": "complete-hash",
                "error": None,
                "data": {"entries": racers()},
            }
            path.write_text(json.dumps(complete), encoding="utf-8")
            partial = {
                **complete,
                "status": "partial",
                "complete": False,
                "content_hash": "partial-hash",
                "data": {"entries": racers()[:3]},
            }
            result = save_document(path, partial, target, "exhibition", CONFIG)
            self.assertTrue(result["preserved"])
            self.assertEqual(json.loads(path.read_text())["content_hash"], "complete-hash")

    def test_same_hash_does_not_rewrite(self) -> None:
        target = {
            "date": "2026-07-24",
            "venue": "a",
            "race_no": 1,
            "deadline": "09:00",
            "racers": racers(),
        }
        document = {
            **{key: target[key] for key in ("date", "venue", "race_no", "deadline")},
            "fetched_at": "2026-07-24T08:30:00+09:00",
            "source": "fixture",
            "status": "complete",
            "complete": True,
            "content_hash": "",
            "error": None,
            "data": {"entries": racers()},
        }
        document["content_hash"] = content_hash(document)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exhibition.json"
            first = save_document(path, document, target, "exhibition", CONFIG)
            mtime = path.stat().st_mtime_ns
            document["fetched_at"] = "2026-07-24T08:34:00+09:00"
            document["content_hash"] = content_hash(document)
            second = save_document(path, document, target, "exhibition", CONFIG)
            self.assertTrue(first["changed"])
            self.assertFalse(second["changed"])
            self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_duplicate_lane_and_player_mismatch(self) -> None:
        target = {
            "date": "2026-07-24",
            "venue": "a",
            "race_no": 1,
            "deadline": "09:00",
            "racers": racers(),
        }
        document = {
            **{key: target[key] for key in ("date", "venue", "race_no", "deadline")},
            "fetched_at": "2026-07-24T08:30:00+09:00",
            "source": "fixture",
            "status": "partial",
            "complete": False,
            "content_hash": "x",
            "error": None,
            "data": {
                "entries": [
                    {"lane": 1, "player_id": "wrong"},
                    {"lane": 1, "player_id": "101"},
                ]
            },
        }
        errors = validate_live_data(document, target, "exhibition")
        self.assertTrue(any("duplicate lane" in error for error in errors))
        self.assertTrue(any("player id mismatch" in error for error in errors))

    def test_process_lock_blocks_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "live.lock"
            with process_lock(lock) as first:
                with process_lock(lock) as second:
                    self.assertTrue(first)
                    self.assertFalse(second)


if __name__ == "__main__":
    unittest.main()
