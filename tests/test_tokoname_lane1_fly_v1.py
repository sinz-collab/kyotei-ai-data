from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from engines.tokoname_v1.backfill_lane1_fly_v1 import backfill_document


ROOT = Path(__file__).resolve().parents[1]


class TokonameLane1FlyPublishedDataTests(unittest.TestCase):
    def test_october_6_to_10_public_json_contract(self) -> None:
        stages = set()
        for compact in ("20261006", "20261007", "20261008", "20261009", "20261010"):
            payload = json.loads(
                (ROOT / "data" / "venues" / "tokoname" / f"{compact}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(payload["races"]), 12)
            for race in payload["races"]:
                prediction = race["prediction"]
                probability = prediction["lane1FlyProbability"]
                self.assertGreaterEqual(probability, 0.0)
                self.assertLessEqual(probability, 100.0)
                self.assertEqual(probability, prediction["lane1FlyDetail"]["probability"])
                self.assertIn(
                    prediction["lane1FlyStage"], {"morning", "post_exhibition"}
                )
                stages.add(prediction["lane1FlyStage"])
                detail = prediction["lane1FlyDetail"]
                self.assertFalse(detail["oddsUsedForPrediction"])
                self.assertFalse(detail["raceActualStartUsedForPrediction"])
                self.assertFalse(detail["resultUsedForPrediction"])
                self.assertFalse(detail["originalExhibitionUsedForPrediction"])
        self.assertEqual(stages, {"morning", "post_exhibition"})

    def test_latest_matches_october_10(self) -> None:
        latest = json.loads(
            (ROOT / "data" / "venues" / "tokoname" / "latest.json").read_text(
                encoding="utf-8"
            )
        )
        dated = json.loads(
            (ROOT / "data" / "venues" / "tokoname" / "20261010.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(latest, dated)

    def test_backfill_is_idempotent_after_all_fly_fields_are_lost(self) -> None:
        document = json.loads(
            (ROOT / "data" / "venues" / "tokoname" / "20261010.json").read_text(
                encoding="utf-8"
            )
        )
        broken = deepcopy(document)
        for race in broken["races"]:
            for key in (
                "lane1FlyProbability",
                "lane1FlyLevel",
                "lane1FlyStage",
                "lane1FlyDetail",
            ):
                race["prediction"].pop(key)

        live_root = ROOT / "data" / "live" / "2026-10-10" / "tokoname"
        first, first_reports = backfill_document(
            broken,
            live_root,
            missing_only=True,
        )
        second, second_reports = backfill_document(
            first,
            live_root,
            missing_only=True,
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [report["status"] for report in first_reports],
            ["post_exhibition"] * 12,
        )
        self.assertEqual(
            [report["reason"] for report in second_reports],
            ["fly_present"] * 12,
        )
        for race in second["races"]:
            prediction = race["prediction"]
            self.assertEqual(
                prediction["lane1FlyProbability"],
                prediction["lane1FlyDetail"]["probability"],
            )


if __name__ == "__main__":
    unittest.main()
