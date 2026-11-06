from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from kiryu_v1_engine import KiryuV1


class KiryuVariableFlyReplayTests(unittest.TestCase):
    def test_november_4_and_5_verified_23_races_reproduce_eight_hits(self) -> None:
        engine = KiryuV1()
        rows = []
        results = json.loads(
            (ROOT / "tests" / "fixtures" / "kiryu_v1" / "verified_20261104_05_results.json").read_text(encoding="utf-8")
        )
        for day in ("2026-11-04", "2026-11-05"):
            compact = day.replace("-", "")
            payload = json.loads(
                (ROOT / "data" / "venues" / "kiryu" / f"{compact}.json").read_text(encoding="utf-8")
            )
            for race in payload["races"]:
                race_no = int(race["race"])
                if day == "2026-11-05" and race_no == 12:
                    continue
                combo = results[day][str(race_no)]
                prediction = engine.predict(race, day)
                tickets = [row["combo"] for row in prediction["flyPrediction"]["tickets"]]
                rows.append((day, race_no, combo, tickets, prediction["flyPrediction"]["allocation"]))

        self.assertEqual(len(rows), 23)
        hits = [row for row in rows if row[2] in row[3]]
        self.assertEqual(len(hits), 8, [(day, race, combo) for day, race, combo, _, _ in hits])
        fly_results = [row for row in rows if not row[2].startswith("1-")]
        self.assertEqual(len(fly_results), 14)
        self.assertEqual(sum(row[2] in row[3] for row in fly_results), 8)
        november_5_r4 = next(row for row in rows if row[0] == "2026-11-05" and row[1] == 4)
        self.assertEqual(november_5_r4[2], "2-6-1")
        self.assertIn("2-6-1", november_5_r4[3])


if __name__ == "__main__":
    unittest.main()
