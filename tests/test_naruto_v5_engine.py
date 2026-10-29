from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
if str(AUTOMATION) not in sys.path:
    sys.path.insert(0, str(AUTOMATION))

from naruto_v5_engine import NarutoV5Engine  # noqa: E402
from run_naruto_v5 import result_evaluation, run_morning, run_result, update_first60_metrics  # noqa: E402
from build_site_data import prediction_payload_gate  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "naruto_v5_replay.json"
LATE_ARTIFACT = AUTOMATION / "models" / "naruto_v5_fixed_20261020.joblib"
EARLY_ARTIFACT = AUTOMATION / "models" / "naruto_v5_replay_20261005.joblib"


class NarutoV5EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.engines = {
            "early": NarutoV5Engine(EARLY_ARTIFACT),
            "late": NarutoV5Engine(LATE_ARTIFACT),
        }

    def test_107_race_v5_normal_and_fly_regression(self) -> None:
        matched = 0
        for row in self.rows:
            engine = self.engines["early" if row["date"] <= "2026-10-09" else "late"]
            prediction = engine.predict_live(
                date=row["date"],
                race_no=row["race"],
                racers=row["racers"],
                live_entries=row["live_entries"],
                wind_speed_mps=row["wind_speed_mps"],
                wave_height_cm=row["wave_height_cm"],
                generated_at="2026-10-29T00:00:00+09:00",
                allow_reference_missing=True,
            )
            normal = [ticket["combo"] for ticket in prediction.normal_tickets]
            fly = [ticket["combo"] for ticket in prediction.fly_tickets]
            self.assertEqual(normal, row["expected_normal"])
            self.assertEqual(fly, row["expected_fly"])
            self.assertEqual(len(set(normal)), 10)
            self.assertEqual(len(set(fly)), 10)
            self.assertTrue(all(not combo.startswith("1-") for combo in fly))
            self.assertAlmostEqual(sum(item["probability"] for item in prediction.probabilities), 1.0, places=12)
            self.assertAlmostEqual(sum(prediction.first), 1.0, places=12)
            self.assertAlmostEqual(sum(prediction.second), 1.0, places=12)
            self.assertAlmostEqual(sum(prediction.third), 1.0, places=12)
            matched += 1
        self.assertEqual(matched, 107)

    def test_live_missing_exhibition_is_rejected_without_invented_values(self) -> None:
        row = self.rows[-1]
        live = [dict(item) for item in row["live_entries"]]
        live[0]["turn_time"] = None
        with self.assertRaisesRegex(ValueError, "naruto_live_features_missing_lane_1:turn_time"):
            self.engines["late"].predict_live(
                date=row["date"],
                race_no=row["race"],
                racers=row["racers"],
                live_entries=live,
                wind_speed_mps=row["wind_speed_mps"],
                wave_height_cm=row["wave_height_cm"],
            )

    def test_morning_model_uses_no_live_input_and_emits_complete_distribution(self) -> None:
        row = self.rows[-1]
        prediction = self.engines["late"].predict_morning(
            date=row["date"],
            race_no=row["race"],
            racers=row["racers"],
            generated_at="2026-10-29T00:00:00+09:00",
        )
        self.assertEqual(prediction.stage, "morning")
        self.assertEqual(len(prediction.probabilities), 120)
        self.assertEqual(len(prediction.normal_tickets), 10)
        self.assertEqual(len(prediction.fly_tickets), 10)
        self.assertTrue(all(not ticket["combo"].startswith("1-") for ticket in prediction.fly_tickets))
        self.assertNotIn("liveEntries", prediction.input_snapshot)

    def test_twelve_race_morning_payload_passes_publication_gate(self) -> None:
        racers = self.rows[-1]["racers"]
        payload = {
            "venueId": "naruto",
            "venue": "鳴門",
            "date": "2026-10-29",
            "engine": "",
            "races": [
                {"race": race_no, "deadline": "12:00", "racers": racers}
                for race_no in range(1, 13)
            ],
            "preds": {},
        }
        generated = run_morning(self.engines["late"], payload)
        self.assertEqual(prediction_payload_gate(generated, "2026-10-29"), (True, "ok"))
        for race_no in range(1, 13):
            prediction = generated["preds"][str(race_no)]
            self.assertEqual(len(prediction["prediction_history"]["morning"]), 1)
            self.assertEqual(prediction["prediction_history"]["live"], [])

    def test_result_evaluation_does_not_rewrite_prediction_snapshot(self) -> None:
        racers = self.rows[-1]["racers"]
        payload = run_morning(
            self.engines["late"],
            {
                "venueId": "naruto",
                "date": "2026-10-29",
                "races": [{"race": 1, "racers": racers}],
                "preds": {},
            },
        )
        frozen = json.dumps(payload["preds"]["1"]["prediction_history"]["morning"][0]["prediction"], sort_keys=True)
        winning_combo = payload["preds"]["1"]["ai"][0]["combo"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "result.json").write_text(
                json.dumps(
                    {
                        "complete": True,
                        "fetched_at": "2026-10-29T13:00:00+09:00",
                        "data": {"order": winning_combo.split("-"), "payout3t": "1,230円"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            evaluated = run_result(payload, 1, root)
        prediction = evaluated["preds"]["1"]
        self.assertEqual(json.dumps(prediction["prediction_history"]["morning"][0]["prediction"], sort_keys=True), frozen)
        self.assertTrue(prediction["resultEvaluation"]["normal"]["hit"])
        self.assertEqual(prediction["resultEvaluation"]["normal"]["return_yen"], 1230)
        self.assertIn("result_evaluation", prediction["prediction_history"]["morning"][0])

    def test_result_evaluation_combined_tickets_remove_overlap(self) -> None:
        prediction = {
            "ai": [{"combo": "2-1-3"}, {"combo": "2-3-1"}],
            "flyPrediction": {"tickets": [{"combo": "2-1-3"}, {"combo": "3-1-2"}]},
        }
        evaluation = result_evaluation(
            prediction,
            {"complete": True, "data": {"order": [2, 1, 3], "payout3t": "2,000円"}},
        )
        self.assertEqual(evaluation["combined_unique"]["ticket_count"], 3)
        self.assertEqual(evaluation["combined_unique"]["stake_yen"], 300)
        self.assertTrue(evaluation["normal_and_fly_hit"])

    def test_first60_metrics_are_idempotent_and_aggregate_all_modes(self) -> None:
        prediction = {
            "modelVersion": "naruto_v5_live_alpha050_6211_fixed_20261020",
            "active_prediction_stage": "live",
            "generatedAt": "2026-10-29T12:00:00+09:00",
            "lane1FlyProbability": 40.0,
            "resultEvaluation": result_evaluation(
                {
                    "ai": [{"combo": "2-1-3"}],
                    "flyPrediction": {"tickets": [{"combo": "2-1-3"}]},
                },
                {"complete": True, "data": {"order": [2, 1, 3], "payout3t": "2,000円"}},
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = update_first60_metrics(root, "2026-10-29", 1, prediction)
            second = update_first60_metrics(root, "2026-10-29", 1, prediction)
        self.assertEqual(len(first["races"]), 1)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["normal"]["hits"], 1)
        self.assertEqual(first["summary"]["fly"]["hits"], 1)
        self.assertEqual(first["summary"]["combined_unique"]["stake_yen"], 100)
        self.assertEqual(first["summary"]["fly_probability"]["actual_rate_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
