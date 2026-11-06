from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
SCRIPTS = ROOT / "scripts"
sys.path[:0] = [str(AUTOMATION), str(SCRIPTS)]

import build_site_data
from boaters_fetch import normalize_stadium, stadium_to_slug
from detect_active_venues import detect_active_venues
from live_fetch_once import apply_kiryu_live_prediction


class KiryuIntegrationTests(unittest.TestCase):
    def test_live_prediction_runs_only_after_all_saved_inputs_complete(self) -> None:
        target = {"venue": "kiryu", "date": "2026-11-05", "race_no": 2}
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b"complete", b""))
        logger = Mock()
        with tempfile.TemporaryDirectory() as temp, patch(
            "live_fetch_once.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ) as create_process:
            race_dir = Path(temp)
            documents = {
                "direct": {"status": "complete", "complete": True, "data": {
                    "racers": [{"lane": lane} for lane in range(1, 7)],
                    "actual_entry": list(range(1, 7)), "wind_speed": 2,
                }},
                "exhibition": {"status": "complete", "complete": True, "data": {
                    "entries": [{"lane": lane, "exhibition_course": lane} for lane in range(1, 7)],
                }},
                "original_exhibition": {"status": "partial", "complete": False, "data": {
                    "entries": [{"lane": lane, "straight_time": 7 + lane / 100} for lane in range(1, 7)],
                }},
            }
            for name, document in documents.items():
                (race_dir / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
            self.assertTrue(asyncio.run(
                apply_kiryu_live_prediction(target, race_dir, {}, logger)
            ))
            self.assertEqual(create_process.await_count, 1)

            (race_dir / "original_exhibition.json").write_text(
                json.dumps({"status": "pending", "complete": False}),
                encoding="utf-8",
            )
            self.assertFalse(asyncio.run(
                apply_kiryu_live_prediction(target, race_dir, {}, logger)
            ))
            self.assertEqual(create_process.await_count, 1)
            self.assertEqual(
                logger.info.call_args.kwargs["extra"]["incomplete_inputs"],
                ["original_exhibition"],
            )

    def test_kiryu_is_a_morning_and_live_prediction_venue(self) -> None:
        venues = json.loads((AUTOMATION / "venues.json").read_text(encoding="utf-8"))["venues"]
        self.assertEqual([venue for venue in venues if venue["slug"] == "kiryu"], [{"slug": "kiryu", "name": "桐生"}])
        self.assertIn(("kiryu", "桐生"), build_site_data.ALL_VENUES)
        self.assertIn("kiryu", build_site_data.PREDICTION_VENUES)
        morning = (AUTOMATION / "publish_morning_data.sh").read_text(encoding="utf-8")
        self.assertIn("run_kiryu_v1.py", morning)
        live = (SCRIPTS / "live_fetch_once.py").read_text(encoding="utf-8")
        self.assertIn("apply_kiryu_live_prediction", live)
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
