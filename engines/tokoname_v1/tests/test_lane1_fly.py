from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ENGINE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_DIR.parents[1]
sys.path.insert(0, str(ENGINE_DIR))

from lane1_fly import predict_morning, predict_post_exhibition  # noqa: E402


class TokonameLane1FlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (REPO_ROOT / "data" / "venues" / "tokoname" / "20261010.json").read_text(
                encoding="utf-8"
            )
        )
        cls.live_root = REPO_ROOT / "data" / "live" / "2026-10-10" / "tokoname"
        cls.model_dir = ENGINE_DIR / "models"

    def test_morning_predictions_are_bounded_and_auditable(self) -> None:
        for race in self.document["races"]:
            prediction = predict_morning(self.document, race, self.model_dir)
            self.assertGreaterEqual(prediction["lane1FlyProbability"], 0.0)
            self.assertLessEqual(prediction["lane1FlyProbability"], 100.0)
            self.assertEqual(prediction["lane1FlyStage"], "morning")
            detail = prediction["lane1FlyDetail"]
            self.assertFalse(detail["oddsUsedForPrediction"])
            self.assertFalse(detail["raceActualStartUsedForPrediction"])
            self.assertFalse(detail["resultUsedForPrediction"])
            self.assertFalse(detail["originalExhibitionUsedForPrediction"])

    def test_post_exhibition_uses_normal_exhibition_and_water_only(self) -> None:
        race = self.document["races"][0]
        race_dir = self.live_root / "01"
        direct = json.loads((race_dir / "direct.json").read_text(encoding="utf-8"))
        exhibition = json.loads(
            (race_dir / "exhibition.json").read_text(encoding="utf-8")
        )
        prediction = predict_post_exhibition(
            self.document, race, direct, exhibition, self.model_dir
        )
        self.assertEqual(prediction["lane1FlyStage"], "post_exhibition")
        self.assertGreaterEqual(prediction["lane1FlyProbability"], 0.0)
        self.assertLessEqual(prediction["lane1FlyProbability"], 100.0)

    def test_missing_exhibition_keeps_final_model_from_running(self) -> None:
        race = self.document["races"][0]
        race_dir = self.live_root / "01"
        direct = json.loads((race_dir / "direct.json").read_text(encoding="utf-8"))
        exhibition = json.loads(
            (race_dir / "exhibition.json").read_text(encoding="utf-8")
        )
        exhibition["data"]["entries"] = exhibition["data"]["entries"][:5]
        with self.assertRaisesRegex(ValueError, "six_exhibition_entries_required"):
            predict_post_exhibition(
                self.document, race, direct, exhibition, self.model_dir
            )


if __name__ == "__main__":
    unittest.main()
