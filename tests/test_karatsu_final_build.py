from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
sys.path.insert(0, str(AUTOMATION))

import build_site_data as builder
from apply_karatsu_v1 import ENGINE_ID, apply_file


DATE = "2026-07-31"
SOURCE = ROOT / "data" / "venues" / "karatsu" / "20260731.json"


def without_prediction_domains(payload: dict) -> dict:
    candidate = deepcopy(payload)
    for key in (
        "engine",
        "engineVersion",
        "preds",
        "predictionStatus",
        "predictionReason",
    ):
        candidate.pop(key, None)
    for race in candidate.get("races") or []:
        race.pop("prediction", None)
    return candidate


class KaratsuFinalBuildTests(unittest.TestCase):
    def test_predictions_survive_the_three_stage_morning_pipeline(self) -> None:
        before_engine = json.loads(SOURCE.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            engine_path = Path(directory) / "karatsu.json"
            engine_path.write_text(
                json.dumps(before_engine, ensure_ascii=False),
                encoding="utf-8",
            )
            apply_file(engine_path)
            after_engine = json.loads(engine_path.read_text(encoding="utf-8"))

            self.assertEqual(after_engine["engine"], ENGINE_ID)
            self.assertEqual(set(after_engine["preds"]), {str(n) for n in range(1, 13)})
            self.assertTrue(
                all(
                    race["prediction"]["status"] == "complete"
                    for race in after_engine["races"]
                )
            )
            self.assertEqual(
                without_prediction_domains(after_engine),
                without_prediction_domains(before_engine),
            )

            final_payload = builder.preserve_prediction_payload(
                deepcopy(before_engine),
                engine_path,
            )
            self.assertIsNotNone(final_payload)
            prediction_available, reason = builder.prediction_payload_gate(
                final_payload,
                DATE,
            )
            self.assertTrue(prediction_available, reason)
            final_payload = builder.attach_independent_race_domains(
                final_payload,
                "karatsu",
                prediction_available,
                "",
            )

            self.assertEqual(final_payload["predictionStatus"], "ready")
            self.assertTrue(
                all(
                    race["prediction"]["status"] == "ready"
                    for race in final_payload["races"]
                )
            )
            self.assertEqual(
                without_prediction_domains(final_payload),
                without_prediction_domains(before_engine),
            )


if __name__ == "__main__":
    unittest.main()
