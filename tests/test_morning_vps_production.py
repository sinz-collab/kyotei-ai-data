from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MorningVpsProductionTest(unittest.TestCase):
    def test_guard_status_is_artifact_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "automation" / "write_morning_fetch_status.py"),
                    "--venue",
                    "kiryu",
                    "--date",
                    "2026-11-17",
                    "--root",
                    str(root),
                    "--reason",
                    "cutoff_before_live",
                ],
                check=True,
                cwd=ROOT,
            )
            status_path = root / "桐生" / "20261117" / "fetch_status.json"
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(
                (status["slug"], status["name"], status["date"]),
                ("kiryu", "桐生", "2026-11-17"),
            )
            self.assertFalse(status["open"])
            self.assertEqual(status["failure"]["reason"], "cutoff_before_live")

    def test_executor_has_live_priority_and_failure_isolation(self) -> None:
        executor = (ROOT / "deploy/morning-vps/sinz-morning-exec").read_text(
            encoding="utf-8"
        )
        for expected in (
            'CPUQuota=50%',
            'MemoryMax=1536M',
            'Nice=10',
            'IOWeight=10',
            'today 08:15',
            'write_morning_fetch_status.py',
            '/run/lock/sinz-morning-fetch-${shard}.lock',
        ):
            self.assertIn(expected, executor)
        self.assertIn('|| true', executor)


if __name__ == "__main__":
    unittest.main()
