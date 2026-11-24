from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import official_morning as official  # noqa: E402
import publish_official_stages as publisher  # noqa: E402


def payload(with_supplements: bool) -> dict:
    races = []
    for race_no in range(1, 13):
        racers = []
        for lane in range(1, 7):
            racer = {
                "lane": lane, "player_id": str(4000 + lane), "name": f"選手 {lane}",
                "class": "A1", "age": "30", "weight": "52.0", "branch": "群馬",
                "f": "0", "l": "0", "avg_st": ".15", "nat_win": "6.00",
                "nat_2": "40.0", "nat_3": "60.0", "local_win": "5.50",
                "local_2": "35.0", "local_3": "55.0", "motor_no": str(lane),
                "motor_2": "30.0", "motor_3": "50.0", "boat_no": str(lane),
                "boat_2": "30.0", "boat_3": "50.0", "season_runs": [],
                "season_groups": [],
            }
            if with_supplements:
                racer.update({
                    "local_st": ".15", "boaters_local_avg_st": "0.15",
                    "boaters_local_st_rank": "3.0位", "boaters_local_st_status": "available",
                    "motor_recent": {"runs_count": 9 if lane == 6 else 10},
                    "boaters_kimarite_starts": 20, "boaters_sashi_rate": 5.0,
                })
            racers.append(racer)
        races.append({
            "race": race_no, "deadline": "10:00", "title": "桐生", "type": "予選",
            "entryFixed": False, "racers": racers,
            "prediction": {"fixed": race_no}, "live": {"fixed": race_no},
        })
    return {
        "venueId": "kiryu", "venue": "桐生", "date": "2026-11-18",
        "races": races, "preds": {"fixed": True}, "predictionStatus": "ready",
    }


class PublishOfficialStagesTests(unittest.TestCase):
    def test_two_stages_preserve_predictions_and_merge_four_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            candidates = root / "candidates" / "kiryu"
            candidates.mkdir(parents=True)
            (data / "venues/kiryu").mkdir(parents=True)
            base = payload(False)
            before = official.protected_projection(base)
            (data / "venues/kiryu/20261118.json").write_text(
                json.dumps(base, ensure_ascii=False), encoding="utf-8"
            )
            (data / "manifest.json").write_text(
                json.dumps({"version": 1, "date": "2026-11-18", "venues": [{"slug": "kiryu"}]}),
                encoding="utf-8",
            )
            (data / "morning_report.json").write_text(
                json.dumps({"date": "2026-11-18", "venues": {}}), encoding="utf-8"
            )
            candidate_path = candidates / "20261118.json"
            candidate_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
            args = argparse.Namespace(
                date="2026-11-18", stage="official", candidate_root=root / "candidates",
                data_root=data, report=None,
            )
            first = publisher.publish(args)
            self.assertEqual(first["publishedRaces"], 12)

            enriched = payload(True)
            candidate_path.write_text(json.dumps(enriched, ensure_ascii=False), encoding="utf-8")
            args.stage = "supplement"
            second = publisher.publish(args)
            published = json.loads((data / "venues/kiryu/20261118.json").read_text(encoding="utf-8"))
            self.assertEqual(official.protected_projection(published), before)
            self.assertEqual(second["addedItems"], 12 * 6 * 4)
            lane6 = published["races"][10]["racers"][5]
            self.assertEqual(lane6["motor_recent"]["runs_count"], 9)
            self.assertEqual(
                published["sourceStatus"]["official_morning"]["supplementCounts"]["missing"], 0
            )


    def test_official_publish_preserves_newer_live_data_and_supplements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            venue_dir = data / "venues/kiryu"
            venue_dir.mkdir(parents=True)
            candidates = root / "candidates"
            candidates.mkdir()
            existing = payload(True)
            existing["preds"] = {"live_revision": 2}
            existing["races"][0]["result"] = {"finish": "1-2-3"}
            existing["races"][0]["odds"] = {"123": 10.5}
            existing["races"][0]["deadline"] = "10:00"
            before = official.protected_projection(existing)
            (venue_dir / "20261118.json").write_text(
                json.dumps(existing, ensure_ascii=False), encoding="utf-8"
            )
            candidate = payload(False)
            candidate["preds"] = {"live_revision": 1}
            candidate["races"][0]["deadline"] = "10:05"
            (candidates / "kiryu.json").write_text(
                json.dumps(candidate, ensure_ascii=False), encoding="utf-8"
            )
            args = argparse.Namespace(
                date="2026-11-18", stage="official",
                candidate_root=candidates, data_root=data, report=None,
            )
            result = publisher.publish(args)
            published = json.loads(
                (venue_dir / "20261118.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["publishedRaces"], 12)
            self.assertEqual(official.protected_projection(published), before)
            self.assertEqual(published["races"][0]["deadline"], "10:05")
            self.assertEqual(
                published["races"][0]["racers"][0]["motor_recent"]["runs_count"], 10
            )


if __name__ == "__main__":
    unittest.main()
