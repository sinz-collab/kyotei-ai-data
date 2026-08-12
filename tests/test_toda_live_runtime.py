from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AUTOMATION = ROOT / "automation"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(AUTOMATION) not in sys.path:
    sys.path.insert(0, str(AUTOMATION))

from apply_toda_live_v5 import apply_file
from live_fetch_once import apply_toda_live_prediction


class FakeLogger:
    def __init__(self) -> None:
        self.events = []

    def info(self, message, extra=None) -> None:
        self.events.append(("info", message, extra))

    def error(self, message, extra=None) -> None:
        self.events.append(("error", message, extra))


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return b'{"reviewed": true}', b""


class TodaLiveRuntimeTests(unittest.TestCase):
    def test_fetch_hook_runs_when_direct_and_exhibition_are_complete(self) -> None:
        target = {"venue": "toda", "date": "2026-08-12", "race_no": 1}
        result = {
            "items": {
                "direct": {"complete": True, "status": "complete"},
                "exhibition": {"complete": True, "status": "complete"},
                "original_exhibition": {"complete": False, "status": "pending"},
            }
        }
        logger = FakeLogger()
        with patch("asyncio.create_subprocess_exec", return_value=FakeProcess()) as launch:
            asyncio.run(apply_toda_live_prediction(target, Path("race"), result, logger))
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(logger.events[-1][2]["event"], "toda_live_prediction_complete")

    def test_20260812_replay_updates_review_without_odds_dependency(self) -> None:
        source_venue = ROOT / "data" / "venues" / "toda" / "20260812.json"
        source_live = ROOT / "data" / "live" / "2026-08-12" / "toda" / "01"
        if not source_venue.is_file() or not source_live.is_dir():
            self.skipTest("2026-08-12 Toda replay fixture is not present")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venue_root = root / "data" / "venues" / "toda"
            venue_root.mkdir(parents=True)
            payload = json.loads(source_venue.read_text(encoding="utf-8"))
            (venue_root / "20260812.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            (venue_root / "latest.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )

            result = apply_file("2026-08-12", 1, root / "data", source_live)
            updated = json.loads((venue_root / "20260812.json").read_text(encoding="utf-8"))
            prediction = updated["preds"]["1"]
            self.assertTrue(result["reviewed"])
            self.assertTrue(result["realtimeApplied"])
            self.assertFalse(result["oddsUsedForProbability"])
            self.assertEqual(
                updated["engine"],
                "toda_prediction_engine_v5_20260809_scenario_temp_ticket_fix",
            )
            self.assertEqual(prediction["probabilityReviewStatus"], "reviewed")
            self.assertEqual(updated["races"][0]["prediction"]["predictionStage"]["label"], "本予想")

    def test_replay_does_not_wait_for_original_exhibition(self) -> None:
        source_venue = ROOT / "data" / "venues" / "toda" / "20260812.json"
        source_live = ROOT / "data" / "live" / "2026-08-12" / "toda" / "02"
        if not source_venue.is_file() or not source_live.is_dir():
            self.skipTest("2026-08-12 Toda replay fixture is not present")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            venue_root = root / "data" / "venues" / "toda"
            live_root = root / "live"
            venue_root.mkdir(parents=True)
            live_root.mkdir()
            payload = json.loads(source_venue.read_text(encoding="utf-8"))
            for name in ("20260812.json", "latest.json"):
                (venue_root / name).write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
            for name in ("direct.json", "exhibition.json"):
                (live_root / name).write_bytes((source_live / name).read_bytes())

            result = apply_file("2026-08-12", 2, root / "data", live_root)
            self.assertTrue(result["reviewed"])
            self.assertTrue(result["realtimeApplied"])
            self.assertFalse(result["originalExhibitionApplied"])
            self.assertFalse(result["oddsUsedForProbability"])


if __name__ == "__main__":
    unittest.main()
