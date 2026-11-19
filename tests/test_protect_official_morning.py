import json
import tempfile
import unittest
from pathlib import Path

from automation.protect_official_morning import restore


def save(root, path, data):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class ProtectOfficialMorningTest(unittest.TestCase):
    def test_protects_official_data_and_other_venues_remain_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp) / "before", Path(temp) / "after"
            date = "2026-11-20"
            save(before, "venues/toda/20261120.json", {
                "date": date, "preds": {"1": {"history": ["LIVE"]}},
                "sourceStatus": {"official_morning": {"status": "published_supplement"}},
            })
            save(before, "venues/toda/latest.json", {"date": date, "live": "original"})
            save(after, "venues/toda/20261120.json", {"date": date, "preds": {}})
            save(after, "venues/toda/latest.json", {"date": date, "live": "overwritten"})
            save(after, "venues/ashiya/20261120.json", {"date": date, "source": "VPS"})
            save(before, "manifest.json", {"date": date, "venues": [
                {"slug": "toda", "eventLabel": "♥️"}, {"slug": "ashiya", "eventLabel": ""}
            ]})
            save(after, "manifest.json", {"date": date, "venues": [
                {"slug": "toda", "eventLabel": ""}, {"slug": "ashiya", "eventLabel": "G3"}
            ]})
            self.assertEqual(restore(before, after, date), ["toda"])
            result = json.loads((after / "venues/toda/20261120.json").read_text())
            self.assertEqual(result["preds"]["1"]["history"], ["LIVE"])
            self.assertEqual(json.loads((after / "venues/toda/latest.json").read_text())["live"], "original")
            manifest = json.loads((after / "manifest.json").read_text())
            self.assertEqual(manifest["venues"][0]["eventLabel"], "♥️")
            self.assertEqual(manifest["venues"][1]["eventLabel"], "G3")
            self.assertEqual(json.loads((after / "venues/ashiya/20261120.json").read_text())["source"], "VPS")

    def test_does_not_override_legacy_when_official_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp) / "before", Path(temp) / "after"
            save(before, "venues/toda/20261120.json", {"date": "2026-11-20"})
            save(after, "venues/toda/20261120.json", {"date": "2026-11-20", "source": "VPS"})
            self.assertEqual(restore(before, after, "2026-11-20"), [])
            self.assertIn('"VPS"', (after / "venues/toda/20261120.json").read_text())


if __name__ == "__main__":
    unittest.main()
