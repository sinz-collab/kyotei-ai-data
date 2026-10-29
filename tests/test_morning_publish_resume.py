import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "automation" / "prepare_morning_artifacts.py"
CONFIG = ROOT / "automation" / "venues.json"
WORKFLOWS = ROOT / ".github" / "workflows"


class MorningPublishArtifactTest(unittest.TestCase):
    target_date = "2026-10-29"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.venues = {item["slug"]: item["name"] for item in config["venues"]}
        self.east = self.root / "east"
        self.west = self.root / "west"
        self.output = self.root / "output"
        east_slugs = {"ashiya", "shimonoseki", "toda", "edogawa", "wakamatsu"}
        for slug, name in self.venues.items():
            artifact = self.east if slug in east_slugs else self.west
            venue_dir = artifact / name / "20261029"
            venue_dir.mkdir(parents=True)
            (venue_dir / "fetch_status.json").write_text(
                json.dumps(
                    {"date": self.target_date, "slug": slug, "name": name},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def tearDown(self):
        self.temp.cleanup()

    def run_validator(self):
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--date",
                self.target_date,
                "--east",
                str(self.east),
                "--west",
                str(self.west),
                "--output",
                str(self.output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_validates_and_merges_all_twelve_statuses(self):
        result = self.run_validator()
        self.assertEqual(result.returncode, 0, result.stderr)
        statuses = list(self.output.glob("*/20261029/fetch_status.json"))
        self.assertEqual(len(statuses), 12)
        self.assertIn("12 venues", result.stdout)

    def test_stops_when_a_venue_status_is_missing(self):
        (self.west / self.venues["naruto"] / "20261029" / "fetch_status.json").unlink()
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("naruto", result.stderr)
        self.assertFalse(self.output.exists())

    def test_stops_on_target_date_mismatch(self):
        status_path = self.east / self.venues["toda"] / "20261029" / "fetch_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["date"] = "2026-10-28"
        status_path.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity mismatch", result.stderr)
        self.assertFalse(self.output.exists())

    def test_stops_on_unexpected_artifact_content(self):
        (self.east / "unexpected.txt").write_text("bad", encoding="utf-8")
        result = self.run_validator()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected path", result.stderr)
        self.assertFalse(self.output.exists())


class MorningPublishWorkflowTest(unittest.TestCase):
    def test_resume_uses_source_run_artifacts_and_common_publisher(self):
        resume = (WORKFLOWS / "resume-morning-publish.yml").read_text(encoding="utf-8")
        normal = (WORKFLOWS / "morning-data.yml").read_text(encoding="utf-8")

        self.assertIn("name: 朝データ公開のみ再開", resume)
        self.assertEqual(resume.count("run-id: ${{ inputs.source_run_id }}"), 2)
        self.assertIn("ref: main", resume)
        self.assertNotIn("automation/fetch_one.py", resume)
        common_call = 'bash automation/publish_morning_data.sh "$TARGET_DATE"'
        self.assertIn(common_call, resume)
        self.assertIn(common_call, normal)


if __name__ == "__main__":
    unittest.main()
