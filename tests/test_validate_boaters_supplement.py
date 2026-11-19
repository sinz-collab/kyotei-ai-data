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
                detail = validator.inspect_race(race_dir, 1)
                self.assertEqual(detail["status"], "ok")
                racers[1]["boaters_local_st_rank"] = "3.0位"
                (race_dir / "race_01_boaters_local_st.json").write_text(
                    json.dumps({"racers": racers}, ensure_ascii=False), encoding="utf-8"
                )
                detail = validator.inspect_race(race_dir, 1)
                self.assertEqual(detail["status"], "partial")
                self.assertIn("lane_2:partial_source_missing", detail["unresolved"])
                self.assertEqual(validator.validate_race(race_dir, 1), [])

    def test_nine_motor_runs_is_available_source_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            race_dir = Path(temporary)
            racers = [
                {"lane": lane, "player_id": str(4000 + lane), "name": f"選手{lane}",
                 "boaters_local_avg_st": "0.15", "boaters_local_st_rank": "3.0位"}
                for lane in range(1, 7)
            ]
            (race_dir / "race_11_boaters_local_st.json").write_text(
                json.dumps({"racers": racers}, ensure_ascii=False), encoding="utf-8"
            )
            (race_dir / "race_11_data.txt").write_text("data", encoding="utf-8")
            (race_dir / "race_11_motor.txt").write_text("motor", encoding="utf-8")
            motors = {lane: {"runs_count": 9 if lane == 6 else 10} for lane in range(1, 7)}
            kimarite = {lane: {"boaters_kimarite_starts": 1} for lane in range(1, 7)}
            with (
                patch.object(validator, "parse_motor_recent_text", return_value=motors),
                patch.object(validator, "parse_race_kimarite", return_value=kimarite),
            ):
                detail = validator.inspect_race(race_dir, 11)
            self.assertEqual(detail["status"], "ok")
            lane6 = next(row for row in detail["items"] if row["lane"] == 6 and row["item"] == "motorRecent")
            self.assertEqual(lane6["status"], "available")
            self.assertEqual(lane6["reason"], "source_has_fewer_than_10_runs")


if __name__ == "__main__":
    unittest.main()
