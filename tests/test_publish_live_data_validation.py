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

from publish_live_data import validate_staged_json


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
            self.assertIn("line 2 column 1", errors[0])

    def test_accepts_valid_staged_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.run_git(root, "init")
            (root / "valid.json").write_text('{"value": 1}\n', encoding="utf-8")
            self.run_git(root, "add", "valid.json")

            self.assertEqual(validate_staged_json(root), [])


if __name__ == "__main__":
    unittest.main()
