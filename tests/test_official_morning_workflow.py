from __future__ import annotations

import unittest
import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
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
        self.assertIn("browser_identity.py", self.workflow)
        self.assertIn('races="$(seq -s, 1 12)"', self.workflow)
        self.assertIn("validate_boaters_supplement.py", self.workflow)
        self.assertIn("--boaters-root", self.workflow)
        self.assertIn("--course-db", self.workflow)
        self.assertIn("--venues all", self.workflow)
        self.assertIn("--races all", self.workflow)
        self.assertIn("actions/cache/restore@v4", self.workflow)
        self.assertIn("actions/cache/save@v4", self.workflow)
        self.assertIn("official-morning-cache", self.workflow)
        self.assertIn("heldVenues", self.workflow)

    def test_vps_supplement_accepts_twelve_and_never_overwrites_valid_cache(self) -> None:
        gateway = (ROOT / "deploy/morning-vps/sinz-morning-gateway").read_text(encoding="utf-8")
        executor = (ROOT / "deploy/morning-vps/sinz-morning-exec").read_text(encoding="utf-8")
        self.assertIn("maximum twelve", gateway)
        self.assertIn("maximum twelve", executor)
        self.assertIn("validate_boaters_supplement.py", executor)
        self.assertIn('Environment=PYTHONPATH=$app_root/automation', executor)
        self.assertIn("Supplement Python dependency preflight failed.", executor)
        self.assertIn("browser_identity.py", gateway)
        self.assertIn("${missing[@]}", executor)
        self.assertIn(".replaced-invalid", executor)

    def test_offline_audit_covers_exactly_the_twelve_vps_routes(self) -> None:
        path = ROOT / "automation/audit_boaters_saved_artifacts.py"
        spec = importlib.util.spec_from_file_location("boaters_offline_audit", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            set(module.VENUES),
            {
                "kiryu", "toda", "heiwajima", "tokoname", "biwako", "naruto",
                "shimonoseki", "wakamatsu", "ashiya", "fukuoka", "karatsu", "omura",
            },
        )


if __name__ == "__main__":
    unittest.main()
