from __future__ import annotations

import json
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
