from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from cleanup_live_data import cleanup


TODAY = date(2026, 7, 24)


class CleanupLiveDataTests(unittest.TestCase):
    def make_root(self, temp: str) -> Path:
        root = Path(temp) / "live"
        root.mkdir()
        return root

    def add_directory(self, root: Path, value: date, content: bytes = b"json") -> Path:
        path = root / value.isoformat()
        path.mkdir()
        (path / "status.json").write_bytes(content)
        return path

    def test_retention_boundary_and_unexpected_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            expired = self.add_directory(root, TODAY - timedelta(days=15))
            retained = self.add_directory(root, TODAY - timedelta(days=14))
            today = self.add_directory(root, TODAY)
            yesterday = self.add_directory(root, TODAY - timedelta(days=1))
            invalid = root / "2026-7-01"
            invalid.mkdir()
            file_entry = root / (TODAY - timedelta(days=30)).isoformat()
            file_entry.write_text("not a directory", encoding="utf-8")

            result = cleanup(root, today=TODAY, expected_root=root)

            self.assertEqual(result["candidate_dates"], [expired.name])
            self.assertFalse(expired.exists())
            self.assertTrue(retained.exists())
            self.assertTrue(today.exists())
            self.assertTrue(yesterday.exists())
            self.assertTrue(invalid.exists())
            self.assertTrue(file_entry.exists())

    def test_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            expired = self.add_directory(root, TODAY - timedelta(days=15), b"123456")
            result = cleanup(root, today=TODAY, dry_run=True, expected_root=root)
            self.assertEqual(result["candidate_count"], 1)
            self.assertEqual(result["candidate_bytes"], 6)
            self.assertTrue(expired.exists())

    def test_unexpected_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            expected = Path(temp) / "expected"
            actual = Path(temp) / "actual"
            expected.mkdir()
            actual.mkdir()
            with self.assertRaises(ValueError):
                cleanup(actual, today=TODAY, expected_root=expected)

    def test_missing_expected_root_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "missing"
            result = cleanup(root, today=TODAY, expected_root=root)
            self.assertTrue(result["root_missing"])
            self.assertEqual(result["candidate_count"], 0)

    @unittest.skipUnless(os.name == "posix", "symbolic link semantics require POSIX")
    def test_top_level_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = self.make_root(temp)
            outside = Path(temp) / "outside"
            outside.mkdir()
            (outside / "keep.json").write_text("keep", encoding="utf-8")
            link = root / (TODAY - timedelta(days=30)).isoformat()
            link.symlink_to(outside, target_is_directory=True)

            result = cleanup(root, today=TODAY, expected_root=root)

            self.assertEqual(result["candidate_count"], 0)
            self.assertTrue(link.is_symlink())
            self.assertTrue((outside / "keep.json").exists())
