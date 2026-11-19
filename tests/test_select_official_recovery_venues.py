import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from select_official_recovery_venues import missing_venues, valid_card


class SelectOfficialRecoveryVenuesTests(unittest.TestCase):
    def test_only_incomplete_venues_are_fetched(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "venues" / "kiryu" / "20261120.json"
            path.parent.mkdir(parents=True)
            card = {"date": "2026-11-20", "races": [
                {"race": i, "deadline": "12:00", "racers": [{}] * 6}
                for i in range(1, 13)
            ]}
            path.write_text(json.dumps(card), encoding="utf-8")
            self.assertTrue(valid_card(path, "2026-11-20"))
            self.assertEqual(len(missing_venues(root, "2026-11-20")), 5)
            card["races"][0]["racers"] = [{}] * 5
            path.write_text(json.dumps(card), encoding="utf-8")
            self.assertFalse(valid_card(path, "2026-11-20"))
            self.assertEqual(len(missing_venues(root, "2026-11-20")), 6)
            path.write_text("[]", encoding="utf-8")
            self.assertFalse(valid_card(path, "2026-11-20"))


if __name__ == "__main__":
    unittest.main()
