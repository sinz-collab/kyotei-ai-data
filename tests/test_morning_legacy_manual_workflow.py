from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/morning-data-legacy-manual.yml"


class MorningLegacyManualWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_is_manual_only_and_restricted_to_jst_today(self) -> None:
        self.assertIn("workflow_dispatch:", self.workflow)
        self.assertNotIn("schedule:", self.workflow)
        self.assertNotIn("push:", self.workflow)
        self.assertIn('GITHUB_REF" = refs/heads/main', self.workflow)
        self.assertIn('target_date" = "$today_jst', self.workflow)

    def test_restores_direct_runner_fetch_without_vps(self) -> None:
        self.assertIn("python automation/fetch_one.py", self.workflow)
        self.assertIn("playwright install --with-deps chromium", self.workflow)
        self.assertNotIn("sinz-morning-gateway", self.workflow)
        self.assertNotIn("MORNING_VPS_", self.workflow)

    def test_shares_lock_and_stops_blocked_fetch_before_publication(self) -> None:
        self.assertIn("group: morning-race-data", self.workflow)
        self.assertIn("cancel-in-progress: false", self.workflow)
        self.assertIn("Stop safely on Cloudflare or HTTP 403", self.workflow)
        self.assertIn('status.get("fetchReturnCode") == 86', self.workflow)
        self.assertIn("needs.validate.result == 'success'", self.workflow)

    def test_only_validated_successes_reach_guarded_publication(self) -> None:
        self.assertIn("prepare_morning_artifacts.py", self.workflow)
        self.assertIn("validate_morning_vps_artifacts.py", self.workflow)
        self.assertIn("Keep only successfully validated race files", self.workflow)
        self.assertIn("MORNING_RECOVERY_MODE: failed", self.workflow)
        self.assertIn("automation/publish_morning_data.sh", self.workflow)


if __name__ == "__main__":
    unittest.main()
