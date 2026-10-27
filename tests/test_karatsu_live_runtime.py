from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_karatsu_v1_live import run
from live_fetch_once import apply_karatsu_live_prediction


SOURCE = ROOT / "data" / "venues" / "karatsu" / "20261027.json"
LIVE_SOURCE = ROOT / "data" / "live" / "2026-10-27" / "karatsu" / "07"


class KaratsuLiveRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_root = Path(self.temp.name) / "data"
        venue_root = self.data_root / "venues" / "karatsu"
        venue_root.mkdir(parents=True)
        source = json.loads(SOURCE.read_text(encoding="utf-8"))
        text = json.dumps(source, ensure_ascii=False, indent=2) + "\n"
        (venue_root / "20261027.json").write_text(text, encoding="utf-8")
        (venue_root / "latest.json").write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_complete_inputs_publish_final_and_preserve_morning(self) -> None:
        before = json.loads((self.data_root / "venues" / "karatsu" / "20261027.json").read_text(encoding="utf-8"))
        morning = deepcopy(
            before["races"][6].get("predictionPre")
            or before["races"][6]["prediction"]
        )
        report = run("2026-10-27", 7, self.data_root, LIVE_SOURCE)
        self.assertEqual(report["status"], "complete")

        dated = self.data_root / "venues" / "karatsu" / "20261027.json"
        latest = dated.with_name("latest.json")
        self.assertEqual(dated.read_bytes(), latest.read_bytes())
        payload = json.loads(dated.read_text(encoding="utf-8"))
        race = payload["races"][6]
        final = race["predictionFinal"]
        self.assertEqual(race["predictionPre"], morning)
        self.assertEqual(race["prediction"], final)
        self.assertEqual(payload["preds"]["7"], final)
        self.assertEqual(final["phase"], "final")
        self.assertEqual(final["finalPredictionStatus"], "complete")
        self.assertEqual(final["predictionStage"]["label"], "本予想")
        self.assertEqual(len(final["ai"]) + len(final["aiUpset"]), 10)
        self.assertIn(final["sab"], {"S", "A", "B"})
        self.assertEqual(len(final["prediction_history"]["morning"]), 1)
        self.assertEqual(len(final["prediction_history"]["live"]), 1)
        self.assertEqual(len(race["live"]["exhibition"]["entries"]), 6)
        self.assertEqual(len(race["live"]["original"]["entries"]), 6)

        run("2026-10-27", 7, self.data_root, LIVE_SOURCE)
        repeated = json.loads(dated.read_text(encoding="utf-8"))["races"][6]["predictionFinal"]
        self.assertEqual(len(repeated["prediction_history"]["morning"]), 1)
        self.assertEqual(len(repeated["prediction_history"]["live"]), 1)

    def test_incomplete_inputs_do_not_change_public_json(self) -> None:
        dated = self.data_root / "venues" / "karatsu" / "20261027.json"
        before = dated.read_bytes()
        with tempfile.TemporaryDirectory() as live_temp:
            live_root = Path(live_temp)
            for name in ("direct", "exhibition", "original_exhibition"):
                document = {
                    "status": "pending",
                    "complete": False,
                    "data": {"racers": [], "entries": []},
                }
                (live_root / f"{name}.json").write_text(json.dumps(document), encoding="utf-8")
            report = run("2026-10-27", 8, self.data_root, live_root)
        self.assertEqual(report["status"], "skipped")
        self.assertEqual(dated.read_bytes(), before)

    def test_live_fetch_invokes_runner_only_for_complete_inputs(self) -> None:
        target = {"venue": "karatsu", "date": "2026-10-27", "race_no": 7}
        complete = {
            "items": {
                name: {"status": "complete", "complete": True}
                for name in ("direct", "exhibition", "original_exhibition")
            }
        }
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b"complete", b""))
        with patch(
            "live_fetch_once.asyncio.create_subprocess_exec",
            AsyncMock(return_value=process),
        ) as create_process:
            import asyncio

            asyncio.run(apply_karatsu_live_prediction(target, LIVE_SOURCE, complete, Mock()))
            self.assertEqual(create_process.await_count, 1)

            incomplete = deepcopy(complete)
            incomplete["items"]["original_exhibition"]["complete"] = False
            asyncio.run(apply_karatsu_live_prediction(target, LIVE_SOURCE, incomplete, Mock()))
            self.assertEqual(create_process.await_count, 1)


if __name__ == "__main__":
    unittest.main()
