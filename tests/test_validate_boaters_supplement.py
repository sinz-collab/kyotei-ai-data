from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import validate_boaters_supplement as validator  # noqa: E402


class BoatersSupplementValidatorTests(unittest.TestCase):
    def test_source_dash_is_valid_but_partial_dash_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            race_dir = Path(temporary)
            racers = [
                {
                    "lane": lane,
                    "name": f"選手{lane}",
                    "boaters_local_avg_st": "" if lane == 2 else "0.15",
                    "boaters_local_st_rank": "" if lane == 2 else "3.0位",
                    "boaters_local_st_status": "source_missing" if lane == 2 else "available",
                }
                for lane in range(1, 7)
            ]
            (race_dir / "race_01_boaters_local_st.json").write_text(
                json.dumps({"racers": racers}, ensure_ascii=False), encoding="utf-8"
            )
            (race_dir / "race_01_data.txt").write_text("data", encoding="utf-8")
            (race_dir / "race_01_motor.txt").write_text("motor", encoding="utf-8")
            motors = {lane: {"runs_count": 10} for lane in range(1, 7)}
            kimarite = {lane: {"boaters_kimarite_starts": 1} for lane in range(1, 7)}
            with (
                patch.object(validator, "parse_motor_recent_text", return_value=motors),
                patch.object(validator, "parse_race_kimarite", return_value=kimarite),
            ):
                self.assertEqual(validator.validate_race(race_dir, 1), [])
                racers[1]["boaters_local_st_rank"] = "3.0位"
                (race_dir / "race_01_boaters_local_st.json").write_text(
                    json.dumps({"racers": racers}, ensure_ascii=False), encoding="utf-8"
                )
                self.assertIn("lane_2:partial_source_missing", validator.validate_race(race_dir, 1))


if __name__ == "__main__":
    unittest.main()
