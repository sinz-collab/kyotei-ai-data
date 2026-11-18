from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/morning-data-official.yml"


class OfficialMorningWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_manual_only_and_read_only(self) -> None:
        self.assertIn("name: 朝のレースデータ【公式出走表】", self.workflow)
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertIn("contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)

    def test_has_rate_limit_validation_and_artifact_but_no_publish(self) -> None:
        self.assertIn("--min-interval 1.0", self.workflow)
        self.assertIn("official_morning.py", self.workflow)
        self.assertIn("upload-artifact@v4", self.workflow)
        self.assertNotIn("publish_morning_data.sh", self.workflow)
        self.assertNotIn("git push", self.workflow)
        self.assertIn("sinz-morning-gateway", self.workflow)
        self.assertIn("supplement-version", self.workflow)
        self.assertIn("maximum three", self.workflow)
        self.assertIn("--boaters-root", self.workflow)
        self.assertIn("--venues all", self.workflow)
        self.assertIn("--races all", self.workflow)
        self.assertIn("actions/cache/restore@v4", self.workflow)
        self.assertIn("actions/cache/save@v4", self.workflow)
        self.assertIn("official-morning-cache", self.workflow)


if __name__ == "__main__":
    unittest.main()
