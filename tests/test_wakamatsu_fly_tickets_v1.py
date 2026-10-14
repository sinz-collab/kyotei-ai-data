from __future__ import annotations

from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from wakamatsu_fly_tickets_v1 import (  # noqa: E402
    METHOD,
    MODEL_VERSION,
    PROBABILITY_CALIBRATION,
    PROBABILITY_METHOD,
    apply_wakamatsu_fly_v1,
    build_fly_prediction,
    fly_level,
    normalize_event_type,
)


def rates(value: float = 10.0) -> dict:
    return {str(lane): value for lane in range(1, 7)}


class WakamatsuFlyTicketsV1Tests(unittest.TestCase):
    def test_published_label_fallback_is_explicit_and_unknown_safe(self) -> None:
        self.assertEqual(normalize_event_type(None, "♥️"), "women")
        self.assertEqual(normalize_event_type(None, "🔰"), "rookie")
        self.assertEqual(normalize_event_type(None, "企業杯"), "unknown")

    def test_event_type_boundaries(self) -> None:
        general = ((54.9, "通常"), (55.0, "注意"), (59.9, "注意"), (60.0, "警戒"),
                   (64.9, "警戒"), (65.0, "危険"))
        special = ((49.9, "通常"), (50.0, "注意"), (54.9, "注意"), (55.0, "警戒"),
                   (59.9, "警戒"), (60.0, "危険"))
        for probability, expected in general:
            self.assertEqual(fly_level(probability, "general"), expected)
        for event_type in ("rookie", "women", "unknown"):
            for probability, expected in special:
                self.assertEqual(fly_level(probability, event_type), expected)
        self.assertIsNone(fly_level(None, "general"))

    def test_builds_deterministic_top10_from_all_100_live_only_candidates(self) -> None:
        prediction = {"win": rates(), "second": rates(), "third": rates()}
        fly = build_fly_prediction(prediction, "live", "general")
        self.assertEqual(fly["modelVersion"], MODEL_VERSION)
        self.assertEqual(fly["method"], METHOD)
        self.assertEqual(fly["candidateCount"], 100)
        self.assertEqual(fly["normalizationCandidateCount"], 120)
        self.assertEqual(fly["probabilityMethod"], PROBABILITY_METHOD)
        self.assertEqual(fly["probabilityCalibration"], PROBABILITY_CALIBRATION)
        self.assertEqual(len(fly["tickets"]), 10)
        self.assertEqual(fly["tickets"][0]["combo"], "2-1-3")
        self.assertEqual(fly["tickets"][1]["combo"], "2-1-4")
        self.assertTrue(all(ticket["role"] == "main" for ticket in fly["tickets"]))
        self.assertTrue(all(not ticket["combo"].startswith("1-") for ticket in fly["tickets"]))
        self.assertEqual(len({ticket["combo"] for ticket in fly["tickets"]}), 10)
        self.assertTrue(all(ticket["probability"] is not None for ticket in fly["tickets"]))

    def test_probability_uses_all_120_combinations_and_sums_to_100(self) -> None:
        prediction = {
            "win": {str(lane): float(7 + lane) for lane in range(1, 7)},
            "second": {str(lane): float(13 - lane) for lane in range(1, 7)},
            "third": {str(lane): float(4 + lane * 2) for lane in range(1, 7)},
        }
        fly = build_fly_prediction(prediction, "live", "general")
        all_candidates = []
        for head in range(1, 7):
            remaining = (lane for lane in range(1, 7) if lane != head)
            for second_lane, third_lane in permutations(remaining, 2):
                score = (
                    prediction["win"][str(head)]
                    * prediction["second"][str(second_lane)]
                    * prediction["third"][str(third_lane)]
                )
                all_candidates.append(((head, second_lane, third_lane), score))
        denominator = sum(score for _, score in all_candidates)
        normalized = [score / denominator * 100 for _, score in all_candidates]
        self.assertEqual(len(all_candidates), 120)
        self.assertAlmostEqual(sum(normalized), 100.0, places=10)
        expected_selection = sorted(
            (item for item in all_candidates if item[0][0] != 1),
            key=lambda item: (-item[1], item[0]),
        )[:10]
        self.assertEqual(
            [ticket["combo"] for ticket in fly["tickets"]],
            ["-".join(map(str, combo)) for combo, _ in expected_selection],
        )
        for ticket, (combo, score) in zip(fly["tickets"], expected_selection):
            self.assertAlmostEqual(ticket["score"], score, places=10)
            self.assertAlmostEqual(ticket["probability"], score / denominator * 100, places=10)

    def test_zero_normalization_total_does_not_invent_probability(self) -> None:
        prediction = {"win": rates(0.0), "second": rates(0.0), "third": rates(0.0)}
        fly = build_fly_prediction(prediction, "morning", "unknown")
        self.assertEqual(fly["candidateCount"], 100)
        self.assertEqual(fly["normalizationCandidateCount"], 120)
        self.assertIsNone(fly["normalizationScoreTotal"])
        self.assertEqual(fly["probabilityStatus"], "unavailable")
        self.assertTrue(all(ticket["probability"] is None for ticket in fly["tickets"]))

    def test_missing_is_not_replaced_with_zero(self) -> None:
        prediction = {"win": rates(), "second": rates(), "third": rates()}
        prediction["third"].pop("6")
        fly = build_fly_prediction(prediction, "morning", "unknown")
        self.assertEqual(fly["status"], "unavailable")
        self.assertEqual(fly["tickets"], [])
        self.assertIn("third.6", fly["missingFields"])

        missing_lane1 = {"win": rates(), "second": rates(), "third": rates()}
        missing_lane1["win"].pop("1")
        build_fly_prediction(missing_lane1, "morning", "unknown")
        self.assertIsNone(missing_lane1["lane1FlyProbability"])
        self.assertIsNone(missing_lane1["lane1FlyLevel"])

    def test_normal_overlap_is_allowed_and_normal_tickets_are_untouched(self) -> None:
        normal = [{"combo": "2-1-3", "role": "本線"}]
        prediction = {
            "win": rates(), "second": rates(), "third": rates(),
            "tickets": deepcopy(normal), "odds": {"2-1-3": 999.0},
        }
        fly = build_fly_prediction(prediction, "morning", "general")
        self.assertIn("2-1-3", [ticket["combo"] for ticket in fly["tickets"]])
        self.assertEqual(prediction["tickets"], normal)
        self.assertEqual(prediction["odds"], {"2-1-3": 999.0})

    def test_odds_and_results_do_not_change_fly_selection(self) -> None:
        base = {"win": rates(12.0), "second": rates(13.0), "third": rates(14.0)}
        first = deepcopy(base)
        first.update({"odds": {"2-1-3": 999.0}, "result": {"trifecta": "2-1-3"}})
        second = deepcopy(base)
        second.update({"odds": {"6-5-4": 1.0}, "result": {"trifecta": "6-5-4"}})
        self.assertEqual(
            build_fly_prediction(first, "live", "general")["tickets"],
            build_fly_prediction(second, "live", "general")["tickets"],
        )

    def test_saved_august_september_october_predictions_reproduce_formula(self) -> None:
        dates = (
            "20260822", "20260823", "20260824", "20260825", "20260826",
            "20260922", "20260927", "20261013", "20261014",
        )
        phase_count = 0
        unavailable_count = 0
        source_counts = {"morning": 0, "live": 0}
        for compact in dates:
            path = ROOT / "data" / "venues" / "wakamatsu" / f"{compact}.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            normal_before = {
                (int(race["race"]), key): deepcopy((race.get(key) or {}).get("tickets"))
                for race in payload["races"]
                for key in ("predictionPre", "predictionFinal")
                if isinstance(race.get(key), dict)
            }
            apply_wakamatsu_fly_v1(payload)
            for race in payload["races"]:
                for key, source in (("predictionPre", "morning"), ("predictionFinal", "live")):
                    prediction = race.get(key)
                    if not isinstance(prediction, dict):
                        continue
                    phase_count += 1
                    source_counts[source] += 1
                    self.assertAlmostEqual(
                        prediction["lane1FlyProbability"],
                        100.0 - float(prediction["win"]["1"]),
                        places=10,
                    )
                    self.assertEqual(prediction["lane1FlyProbabilitySource"], source)
                    fly = prediction["flyPrediction"]
                    self.assertEqual(fly["source"], source)
                    if fly["status"] != "complete":
                        unavailable_count += 1
                        self.assertEqual(fly["tickets"], [])
                        continue
                    self.assertEqual(fly["candidateCount"], 100)
                    self.assertEqual(fly["normalizationCandidateCount"], 120)
                    self.assertEqual(len(fly["tickets"]), 10)
                    self.assertEqual(len({row["combo"] for row in fly["tickets"]}), 10)
                    self.assertTrue(all(not row["combo"].startswith("1-") for row in fly["tickets"]))
                    expected = []
                    for head in range(2, 7):
                        remaining = (lane for lane in range(1, 7) if lane != head)
                        for second_lane, third_lane in permutations(remaining, 2):
                            score = (
                                float(prediction["win"][str(head)])
                                * float(prediction["second"][str(second_lane)])
                                * float(prediction["third"][str(third_lane)])
                            )
                            expected.append(((head, second_lane, third_lane), score))
                    expected.sort(key=lambda row: (-row[1], row[0]))
                    self.assertEqual(
                        [row["combo"] for row in fly["tickets"]],
                        ["-".join(map(str, combo)) for combo, _ in expected[:10]],
                    )
                    for ticket, (_, expected_score) in zip(fly["tickets"], expected[:10]):
                        self.assertAlmostEqual(ticket["score"], expected_score, places=8)
                    all_scores = []
                    for head in range(1, 7):
                        remaining = (lane for lane in range(1, 7) if lane != head)
                        for second_lane, third_lane in permutations(remaining, 2):
                            all_scores.append(
                                float(prediction["win"][str(head)])
                                * float(prediction["second"][str(second_lane)])
                                * float(prediction["third"][str(third_lane)])
                            )
                    denominator = sum(all_scores)
                    self.assertGreater(denominator, 0)
                    self.assertAlmostEqual(
                        sum(score / denominator * 100 for score in all_scores),
                        100.0,
                        places=10,
                    )
                    for ticket in fly["tickets"]:
                        self.assertAlmostEqual(
                            ticket["probability"],
                            ticket["score"] / denominator * 100,
                            places=8,
                        )
                    self.assertEqual(
                        prediction["tickets"], normal_before[(int(race["race"]), key)]
                    )
        self.assertGreaterEqual(phase_count, 200)
        self.assertGreater(source_counts["morning"], 0)
        self.assertGreater(source_counts["live"], 0)
        self.assertGreaterEqual(unavailable_count, 0)


if __name__ == "__main__":
    unittest.main()
