from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

from engines.tokoname_v1.fly_tickets_v4 import (
    MODEL_VERSION,
    FlyTicketsUnavailable,
    predict,
    validate_prediction,
    waiting_prediction,
)


ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "engines" / "tokoname_v1" / "models"


class TokonameFlyTicketsV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document = json.loads(
            (ROOT / "data" / "venues" / "tokoname" / "20261019.json").read_text(
                encoding="utf-8"
            )
        )
        cls.race = cls.document["races"][0]
        live = ROOT / "data" / "live" / "2026-10-19" / "tokoname" / "01"
        cls.direct = json.loads((live / "direct.json").read_text(encoding="utf-8"))
        cls.exhibition = json.loads(
            (live / "exhibition.json").read_text(encoding="utf-8")
        )

    def run_prediction(self) -> dict:
        return predict(
            self.document,
            self.race,
            self.direct,
            self.exhibition,
            MODEL_DIR,
        )

    def test_attached_validation_counts_are_exact(self) -> None:
        metadata = json.loads(
            (
                MODEL_DIR
                / "tokoname_fly_tickets_v4"
                / "metadata.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["validation"]["n"], 937)
        self.assertEqual(metadata["validation"]["hit"], 330)
        self.assertEqual(metadata["validation"]["rate"], 35.22)
        self.assertEqual(metadata["test"]["n"], 1153)
        self.assertEqual(metadata["test"]["hit"], 450)
        self.assertEqual(metadata["test"]["rate"], 39.03)

    def test_real_live_input_is_reproducible_and_has_ten_unique_fly_tickets(self) -> None:
        first = self.run_prediction()
        second = self.run_prediction()
        self.assertEqual(first, second)
        validate_prediction(first, allow_waiting=False)
        combos = [ticket["combo"] for ticket in first["tickets"]]
        self.assertEqual(
            combos,
            [
                "2-1-3", "2-1-5", "2-1-4", "3-1-5", "3-1-2",
                "2-1-6", "3-1-4", "3-1-6", "5-1-2", "5-1-3",
            ],
        )
        self.assertEqual(len(set(combos)), 10)
        self.assertTrue(all(not combo.startswith("1-") for combo in combos))
        self.assertNotIn("probability", first)

    def test_lane1_fly_probability_below_fifty_does_not_gate_generation(self) -> None:
        document = deepcopy(self.document)
        document["races"][0].setdefault("prediction", {})[
            "lane1FlyProbability"
        ] = 1.0
        actual = predict(
            document,
            document["races"][0],
            self.direct,
            self.exhibition,
            MODEL_DIR,
        )
        self.assertEqual(actual["status"], "complete")
        self.assertEqual(len(actual["tickets"]), 10)

    def test_prohibited_inputs_are_not_part_of_the_inference_contract(self) -> None:
        actual = self.run_prediction()
        self.assertEqual(
            actual["inputAudit"],
            {
                "oddsUsed": False,
                "resultUsed": False,
                "raceActualStartUsed": False,
                "originalExhibitionUsed": False,
                "normalExhibitionUsed": True,
            },
        )

    def test_missing_exhibition_waits_without_fake_tickets(self) -> None:
        missing = deepcopy(self.exhibition)
        missing["data"]["entries"] = missing["data"]["entries"][:5]
        with self.assertRaises(FlyTicketsUnavailable):
            predict(self.document, self.race, self.direct, missing, MODEL_DIR)
        waiting = waiting_prediction()
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(waiting["tickets"], [])
        self.assertEqual(waiting["modelVersion"], MODEL_VERSION)


if __name__ == "__main__":
    unittest.main()
