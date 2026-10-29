from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "data" / "posthoc" / "naruto" / "20261029"


class NarutoPosthoc20261029Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(
            (REPORT_ROOT / "naruto_v5_posthoc_20261029.json").read_text(encoding="utf-8")
        )

    def test_report_is_isolated_posthoc_output(self) -> None:
        self.assertEqual(self.report["report_type"], "posthoc_verification")
        self.assertIn("当時の本予想の再現ではない", self.report["disclaimer"])
        self.assertEqual(
            self.report["separation"]["prediction_inputs"],
            ["direct", "exhibition", "original_exhibition"],
        )
        self.assertEqual(
            self.report["separation"]["excluded_from_prediction"],
            ["odds", "result", "payout", "kimarite"],
        )
        self.assertFalse(self.report["separation"]["existing_predictions_overwritten"])
        self.assertFalse(self.report["separation"]["existing_metrics_updated"])
        self.assertFalse(self.report["separation"]["site_published"])

    def test_all_twelve_races_are_recalculated_from_complete_live_inputs(self) -> None:
        self.assertEqual(self.report["source_summary"]["races_with_complete_prediction_inputs"], 12)
        self.assertEqual(self.report["recalculation"]["successful_races"], 12)
        self.assertEqual(self.report["recalculation"]["unavailable_races"], [])
        self.assertEqual(self.report["verification"]["verified_races"], 12)
        for race in self.report["races"]:
            self.assertEqual(race["recalculation_status"], "complete")
            self.assertEqual(race["verification_status"], "complete")
            self.assertEqual(len(race["normal_tickets"]), 10)
            self.assertEqual(len(set(race["normal_tickets"])), 10)
            self.assertEqual(len(race["fly_tickets"]), 10)
            self.assertEqual(len(set(race["fly_tickets"])), 10)
            self.assertTrue(all(not ticket.startswith("1-") for ticket in race["fly_tickets"]))
            self.assertEqual(sorted(int(lane) for lane in race["live_entries"]), list(range(1, 7)))
            for field in ("first_pct", "second_pct", "third_pct"):
                self.assertAlmostEqual(sum(race[field].values()), 100.0, places=4)

    def test_metrics_use_two_independent_ten_ticket_stakes(self) -> None:
        verification = self.report["verification"]
        self.assertEqual(verification["normal"]["stake_yen"], 12_000)
        self.assertEqual(verification["fly"]["stake_yen"], 12_000)
        self.assertEqual(verification["combined"]["stake_yen"], 24_000)
        self.assertEqual(verification["normal"]["hits"], 2)
        self.assertEqual(verification["fly"]["hits"], 4)
        self.assertEqual(verification["combined"]["hits"], 5)
        self.assertEqual(verification["both_hit_races"], [5])

    def test_source_inventory_records_every_item_and_no_missing_data(self) -> None:
        with (REPORT_ROOT / "live_source_inventory.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 60)
        self.assertTrue(all(row["available"] == "True" for row in rows))
        self.assertTrue(all(row["complete"] == "True" for row in rows))
        self.assertTrue(all(row["source"] for row in rows))
        self.assertTrue(all(row["fetched_at"] for row in rows))
        self.assertTrue(all(row["sha256"] for row in rows))


if __name__ == "__main__":
    unittest.main()
