from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from public_json import serialize_public_json, write_public_json_atomic


class PublicJsonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "venue": "芦屋",
            "probabilities": {"1": 33.3, "2": 22.2},
            "tickets": ["1-2-3", "2-1-3"],
            "history": [{"stage": "morning"}, {"stage": "live"}],
            "result": {"order": [1, 2, 3]},
        }

    def test_compact_serialization_preserves_structure(self) -> None:
        serialized = serialize_public_json(self.payload)

        self.assertEqual(json.loads(serialized), self.payload)
        self.assertTrue(serialized.endswith("\n"))
        self.assertNotIn("\n  ", serialized)
        self.assertNotIn(": ", serialized)

    def test_atomic_write_preserves_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "venues" / "ashiya" / "latest.json"
            write_public_json_atomic(path, self.payload)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.payload)
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
