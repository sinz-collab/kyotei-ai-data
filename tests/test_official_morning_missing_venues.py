from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from run_official_predictions import COMMANDS, missing_races
import run_karatsu_official


class MissingOfficialPredictionsTests(unittest.TestCase):
    def test_both_venues_registered(self):
        self.assertEqual(COMMANDS["wakamatsu"], ("apply_wakamatsu_v2_3.py",))
        self.assertEqual(COMMANDS["karatsu"], ("run_karatsu_official.py",))
        for venue in ("wakamatsu", "karatsu"):
            self.assertTrue((ROOT / "automation" / COMMANDS[venue][0]).is_file())

    def test_no_post_deadline_prediction_backfill(self):
        payload = {
            "date": "2026-11-26",
            "races": [{"race": 1, "deadline": "15:19"},
                      {"race": 2, "deadline": "19:00"}],
        }
        now = datetime(2026, 11, 26, 16, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
        self.assertEqual(missing_races(payload, now), {2})

    def test_karatsu_runner_uses_requested_isolated_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "venues" / "karatsu" / "20261126.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"preds": {"1": {}}, "engine": "test"}), encoding="utf-8")
            with patch.object(sys, "argv", ["run_karatsu_official.py", "--date",
                                            "2026-11-26", "--data-root", directory]):
                with patch.object(run_karatsu_official, "apply_file") as applied:
                    self.assertEqual(run_karatsu_official.main(), 0)
                applied.assert_called_once_with(path)


if __name__ == "__main__":
    unittest.main()
