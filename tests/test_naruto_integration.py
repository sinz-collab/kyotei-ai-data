from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
SCRIPTS = ROOT / "scripts"
for path in (AUTOMATION, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import build_site_data
from detect_active_venues import detect_active_venues


class NarutoIntegrationTests(unittest.TestCase):
    def test_morning_config_and_manifest_register_naruto_prediction_engine(self) -> None:
        venues = json.loads((AUTOMATION / "venues.json").read_text(encoding="utf-8"))["venues"]
        naruto = [venue for venue in venues if venue["slug"] == "naruto"]

        self.assertEqual(naruto, [{"slug": "naruto", "name": "鳴門"}])
        self.assertIn(("naruto", "鳴門"), build_site_data.ALL_VENUES)
        self.assertIn("naruto", build_site_data.PREDICTION_VENUES)

    def test_naruto_is_fetched_by_morning_workflow_and_supported_by_scoped_live_publish(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "morning-data.yml").read_text(encoding="utf-8")
        publisher = (SCRIPTS / "publish_live_data.py").read_text(encoding="utf-8")

        self.assertIn('venues: "tokoname omura karatsu fukuoka heiwajima biwako naruto"', workflow)
        self.assertIn(
            'choices=("ashiya", "fukuoka", "karatsu", "naruto", "tokoname", "wakamatsu")',
            publisher,
        )

    def test_generic_live_detector_accepts_complete_naruto_12_race_payload(self) -> None:
        target_date = "2026-10-28"
        races = [
            {
                "race": race_no,
                "deadline": f"{8 + race_no // 2:02d}:{(race_no % 2) * 30:02d}",
                "racers": [{"lane": lane} for lane in range(1, 7)],
            }
            for race_no in range(1, 13)
        ]
        payload = {
            "venueId": "naruto",
            "venue": "鳴門",
            "date": target_date,
            "races": races,
            "preds": {},
            "predictionStatus": "unavailable",
            "predictionReason": "venue_engine_not_registered",
        }
        manifest = {
            "date": target_date,
            "venues": [{
                "slug": "naruto",
                "name": "鳴門",
                "date": target_date,
                "open": True,
                "entryCount": 12,
                "dataPath": "venues/naruto/20261028.json",
                "predictionStatus": "unavailable",
            }],
        }

        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            payload_path = data_root / "venues" / "naruto" / "20261028.json"
            payload_path.parent.mkdir(parents=True)
            payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            manifest_path = data_root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            active = detect_active_venues(manifest_path, target_date)

        self.assertEqual([venue["slug"] for venue in active], ["naruto"])
        self.assertEqual(len(active[0]["payload"]["races"]), 12)
        self.assertEqual(active[0]["payload"]["preds"], {})


if __name__ == "__main__":
    unittest.main()
