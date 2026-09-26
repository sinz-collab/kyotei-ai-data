from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from publish_live_data import isolate_invalid_staged_json, validate_staged_json


class StagedJsonValidationTest(unittest.TestCase):
    def run_git(self, root: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_rejects_conflict_markers_in_staged_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_git(root, "init")
            path = root / "broken.json"
            path.write_text(
                '{\n<<<<<<< Updated upstream\n"value": 1\n=======\n'
                '"value": 2\n>>>>>>> Stashed changes\n}\n',
                encoding="utf-8",
            )
            self.run_git(root, "add", "broken.json")

            errors = validate_staged_json(root)

            self.assertEqual(len(errors), 1)
            self.assertIn("broken.json", errors[0])
            self.assertIn("git_conflict_marker: <<<<<<<", errors[0])

    def test_accepts_valid_staged_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_git(root, "init")
            (root / "valid.json").write_text('{"value": 1}\n', encoding="utf-8")
            self.run_git(root, "add", "valid.json")

            self.assertEqual(validate_staged_json(root), [])

    def test_valid_json_remains_staged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.initialized_repo(root)
            valid = root / "data" / "live" / "2026-09-26" / "wakamatsu" / "04" / "direct.json"
            valid.parent.mkdir(parents=True)
            valid.write_text('{"status": "complete"}\n', encoding="utf-8")
            self.run_git(root, "add", "data")

            self.assertEqual(isolate_invalid_staged_json(root), [])
            staged = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            self.assertEqual(staged, [valid.relative_to(root).as_posix()])

    def initialized_repo(self, root: Path) -> None:
        self.run_git(root, "init")
        self.run_git(root, "config", "user.name", "test")
        self.run_git(root, "config", "user.email", "test@example.com")
        (root / ".gitkeep").write_text("", encoding="utf-8")
        self.run_git(root, "add", ".gitkeep")
        self.run_git(root, "commit", "-m", "initial")

    def assert_only_wakamatsu_remains_staged(
        self,
        root: Path,
        fukuoka_content: str,
        expected_reason: str,
    ) -> None:
        self.initialized_repo(root)
        fukuoka_venue = root / "data" / "venues" / "fukuoka" / "20260926.json"
        fukuoka_live = root / "data" / "live" / "2026-09-26" / "fukuoka" / "04" / "direct.json"
        wakamatsu_live = root / "data" / "live" / "2026-09-26" / "wakamatsu" / "04" / "direct.json"
        for path, content in (
            (fukuoka_venue, fukuoka_content),
            (fukuoka_live, '{"status": "complete"}\n'),
            (wakamatsu_live, '{"status": "complete"}\n'),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.run_git(root, "add", "data")

        messages = isolate_invalid_staged_json(root)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()

        self.assertEqual(staged, [wakamatsu_live.relative_to(root).as_posix()])
        self.assertEqual(len(messages), 1)
        self.assertIn(expected_reason, messages[0])
        self.assertEqual(fukuoka_venue.read_text(encoding="utf-8"), fukuoka_content)

    def test_conflict_marker_skips_only_broken_venue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_only_wakamatsu_remains_staged(
                Path(temporary),
                '{\n<<<<<<< Updated upstream\n"value": 1\n=======\n'
                '"value": 2\n>>>>>>> Stashed changes\n}\n',
                "git_conflict_marker: <<<<<<<",
            )

    def test_parse_error_skips_only_broken_venue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.assert_only_wakamatsu_remains_staged(
                Path(temporary),
                '{"value": }\n',
                "json_parse_error:",
            )


if __name__ == "__main__":
    unittest.main()
