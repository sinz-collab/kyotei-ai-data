import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from restore_morning_tides import restore


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class MorningTideRecoveryTest(unittest.TestCase):
    def test_restores_only_tide_and_preserves_live_and_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "venues.json"
            save(config, {"venues": [{"slug": "fukuoka", "name": "福岡", "tide_url": "sample"}]})
            path = root / "data/venues/fukuoka/20261119.json"
            latest = path.parent / "latest.json"
            original = {"date": "2026-11-19", "preds": {"7": {"history": ["LIVE"], "lane1FlyProbability": None}}}
            save(path, original)
            save(latest, {**original, "live": "latest"})
            tide = {"date": "2026-11-19", "status": "ok", "events": [{"type": "満潮", "time": "12:30"}]}

            def fake_fetch(venue, date, directory):
                save(directory / "tide_today.json", tide)
                return {"status": "ok"}

            result = restore("2026-11-19", root / "data", config, root / "cache", fake_fetch)
            self.assertEqual(result, {"fukuoka": "restored"})
            after = json.loads(path.read_text())
            self.assertEqual(after["preds"], original["preds"])
            self.assertEqual(after["tide"], tide)
            self.assertEqual(json.loads(latest.read_text())["live"], "latest")
            self.assertEqual(restore("2026-11-19", root / "data", config, root / "cache", fake_fetch), {"fukuoka": "already_available"})

    def test_does_not_publish_stale_tide(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "venues.json"
            save(config, {"venues": [{"slug": "toda", "name": "戸田", "tide_url": "sample"}]})
            path = root / "data/venues/toda/20261119.json"
            save(path, {"date": "2026-11-19"})

            def fake_fetch(venue, date, directory):
                save(directory / "tide_today.json", {"status": "ok", "date": "2026-11-18", "events": [{"time": "12:00"}]})
                return {"status": "ok"}

            result = restore("2026-11-19", root / "data", config, root / "cache", fake_fetch)
            self.assertEqual(result, {"toda": "invalid_tide_cache"})
            self.assertNotIn("tide", json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
