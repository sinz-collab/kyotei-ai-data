from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
SCRIPTS = ROOT / "scripts"
sys.path[:0] = [str(AUTOMATION), str(SCRIPTS)]

import build_site_data
from boaters_fetch import normalize_stadium, stadium_to_slug
from detect_active_venues import detect_active_venues


class KiryuIntegrationTests(unittest.TestCase):
    def test_kiryu_is_a_morning_and_live_venue_without_prediction_engine(self) -> None:
        venues = json.loads((AUTOMATION / "venues.json").read_text(encoding="utf-8"))["venues"]
        self.assertEqual([venue for venue in venues if venue["slug"] == "kiryu"], [{"slug": "kiryu", "name": "桐生"}])
        self.assertIn(("kiryu", "桐生"), build_site_data.ALL_VENUES)
        self.assertNotIn("kiryu", build_site_data.PREDICTION_VENUES)
        self.assertEqual(normalize_stadium("kiryu"), "桐生")
        self.assertEqual(normalize_stadium("桐生"), "桐生")
        self.assertEqual(stadium_to_slug("桐生"), "kiryu")

        workflow = (ROOT / ".github" / "workflows" / "morning-data.yml").read_text(encoding="utf-8")
        self.assertIn('venues: "kiryu ashiya shimonoseki toda wakamatsu"', workflow)
        publisher = (SCRIPTS / "publish_live_data.py").read_text(encoding="utf-8")
        self.assertIn('"wakamatsu", "kiryu"', publisher)

    def test_generic_live_detector_accepts_complete_kiryu_payload(self) -> None:
        target_date = "2026-11-03"
        payload = {
            "date": target_date,
            "venueId": "kiryu",
            "venue": "桐生",
            "races": [
                {"race": race_no, "deadline": f"{14 + race_no // 3:02d}:{(race_no * 7) % 60:02d}", "racers": [{"lane": lane} for lane in range(1, 7)]}
                for race_no in range(1, 13)
            ],
            "predictionAvailable": False,
            "predictionStatus": "unavailable",
            "predictionReason": "venue_engine_not_registered",
        }
        manifest = {
            "date": target_date,
            "venues": [{
                "slug": "kiryu", "name": "桐生", "date": target_date,
                "open": True, "entryCount": 12, "dataPath": "venues/kiryu/20261103.json",
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payload_path = root / "venues" / "kiryu" / "20261103.json"
            payload_path.parent.mkdir(parents=True)
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            active = detect_active_venues(manifest_path, target_date)
        self.assertEqual([venue["slug"] for venue in active], ["kiryu"])


if __name__ == "__main__":
    unittest.main()
