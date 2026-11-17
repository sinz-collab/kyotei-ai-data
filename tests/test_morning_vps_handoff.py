from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MorningVpsHandoffTest(unittest.TestCase):
    def test_dummy_shards_keep_existing_artifact_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifacts = root / "artifacts"
            merged = root / "merged"
            for shard in ("east", "west"):
                subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "automation" / "make_dummy_morning_artifact.py"),
                        "--date",
                        "2026-11-17",
                        "--shard",
                        shard,
                        "--output",
                        str(artifacts / shard),
                    ],
                    cwd=ROOT,
                    check=True,
                )
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "automation" / "prepare_morning_artifacts.py"),
                    "--date",
                    "2026-11-17",
                    "--east",
                    str(artifacts / "east"),
                    "--west",
                    str(artifacts / "west"),
                    "--output",
                    str(merged),
                ],
                cwd=ROOT,
                check=True,
            )
            statuses = list(merged.glob("*/20261117/fetch_status.json"))
            self.assertEqual(len(statuses), 12)
            self.assertTrue(
                all(
                    json.loads(path.read_text(encoding="utf-8"))["handoffTest"]
                    for path in statuses
                )
            )

    def test_handoff_workflow_is_manual_read_only_and_dummy_only(self) -> None:
        workflow = (
            ROOT / ".github/workflows/morning-vps-handoff-test.yml"
        ).read_text(encoding="utf-8")
        self.assertNotIn("schedule:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn('"dummy $TARGET_DATE east"', workflow)
        self.assertIn('"dummy $TARGET_DATE west"', workflow)
        self.assertNotIn("git push", workflow)
        self.assertNotIn("publish_morning_data.sh", workflow)

    def test_production_workflow_is_not_switched_to_vps(self) -> None:
        workflow = (ROOT / ".github/workflows/morning-data.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("python automation/fetch_one.py", workflow)
        self.assertNotIn("sinz-morning-gateway", workflow)


if __name__ == "__main__":
    unittest.main()
