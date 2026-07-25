from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

import build_site_data as builder


DATE = "2026-07-25"


def probabilities() -> dict[str, float]:
    return {"1": 30.0, "2": 20.0, "3": 15.0, "4": 15.0, "5": 10.0, "6": 10.0}


def prediction() -> dict:
    return {
        "win": probabilities(),
        "second": probabilities(),
        "third": probabilities(),
        "sab": "A",
        "ai": [{"combo": "1-2-3"}],
        "realtime": {"last": {"1": {"time": 6.70}}},
        "odds": {"1-2-3": 12.3},
        "result": {"status": "ok", "order": "1-2-3"},
    }


def race_payload(
    *,
    date: str = DATE,
    slug: str = "toda",
    name: str = "戸田",
    predictions: bool = False,
) -> dict:
    return {
        "venueId": slug,
        "venue": name,
        "date": date,
        "engine": "toda_prediction_engine_20260707" if predictions else "",
        "eventDay": 2,
        "eventDayLabel": "2日目",
        "races": [
            {
                "race": race_no,
                "deadline": f"{9 + race_no // 6:02d}:{(race_no * 5) % 60:02d}",
                "racers": [
                    {
                        "lane": lane,
                        "name": f"選手{lane}",
                        "season_runs": [{"race": "1R", "finish": "2着"}],
                        "season_groups": [{"day": "初日", "runs": [{"race": "1R", "finish": "2着"}]}],
                    }
                    for lane in range(1, 7)
                ],
            }
            for race_no in range(1, 13)
        ],
        "preds": {str(race_no): prediction() for race_no in range(1, 13)} if predictions else {},
        "tide": {},
    }


class PublicationGateUnitTests(unittest.TestCase):
    def test_race_data_gate_accepts_12_races_six_lanes_and_deadlines(self) -> None:
        self.assertEqual(
            builder.race_data_gate(race_payload(), DATE, "toda", "戸田"),
            (True, "ok"),
        )

    def test_race_data_gate_rejects_wrong_date_and_venue(self) -> None:
        wrong_date = race_payload(date="2026-07-24")
        wrong_venue = race_payload(slug="ashiya")
        self.assertEqual(
            builder.race_data_gate(wrong_date, DATE, "toda", "戸田")[1],
            "race_date_mismatch",
        )
        self.assertEqual(
            builder.race_data_gate(wrong_venue, DATE, "toda", "戸田")[1],
            "race_venue_mismatch",
        )

    def test_race_data_gate_rejects_missing_race_lane_and_deadline(self) -> None:
        cases = []
        missing_race = race_payload()
        missing_race["races"].pop()
        cases.append((missing_race, "race_count_invalid"))
        missing_lane = race_payload()
        missing_lane["races"][0]["racers"].pop()
        cases.append((missing_lane, "race_01_entry_count_invalid"))
        bad_deadline = race_payload()
        bad_deadline["races"][0]["deadline"] = ""
        cases.append((bad_deadline, "race_01_deadline_invalid"))
        for payload, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(
                    builder.race_data_gate(payload, DATE, "toda", "戸田")[1],
                    reason,
                )

    def test_prediction_payload_gate_accepts_complete_venue_engine(self) -> None:
        self.assertEqual(
            builder.prediction_payload_gate(race_payload(predictions=True), DATE),
            (True, "ok"),
        )

    def test_prediction_payload_gate_rejects_missing_prediction_without_blocking_race_data(self) -> None:
        payload = race_payload()
        self.assertTrue(builder.race_data_gate(payload, DATE, "toda", "戸田")[0])
        self.assertFalse(builder.prediction_payload_gate(payload, DATE)[0])

    def test_prediction_payload_gate_rejects_baseline_fallback_and_bad_normalization(self) -> None:
        baseline = race_payload(predictions=True)
        baseline["engine"] = "deterministic_baseline_v1"
        fallback = race_payload(predictions=True)
        fallback["fallbackUsed"] = True
        bad_total = race_payload(predictions=True)
        bad_total["preds"]["1"]["win"]["1"] = 60.0
        for payload in (baseline, fallback, bad_total):
            with self.subTest(engine=payload.get("engine")):
                self.assertFalse(builder.prediction_payload_gate(payload, DATE)[0])


class PublicationGateIntegrationTests(unittest.TestCase):
    def run_builder(
        self,
        root: Path,
        morning: dict | None,
        *,
        open_venue: bool = True,
        existing_today: dict | None = None,
        existing_previous: dict | None = None,
    ) -> tuple[dict, Path]:
        source_root = root / "source"
        data_root = root / "data"
        source_dir = source_root / "戸田" / "20260725"
        source_dir.mkdir(parents=True)
        (source_dir / "fetch_status.json").write_text(
            json.dumps(
                {
                    "date": DATE,
                    "slug": "toda",
                    "name": "戸田",
                    "open": open_venue,
                    "entryCount": 12 if open_venue else 0,
                    "precheck": {"reason": "race_page_found" if open_venue else "not_scheduled"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = root / "venues.json"
        config_path.write_text(
            json.dumps({"venues": [{"slug": "toda", "name": "戸田"}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        venue_dir = data_root / "venues" / "toda"
        if existing_today is not None:
            venue_dir.mkdir(parents=True, exist_ok=True)
            for filename in ("20260725.json", "latest.json"):
                (venue_dir / filename).write_text(
                    json.dumps(existing_today, ensure_ascii=False),
                    encoding="utf-8",
                )
        if existing_previous is not None:
            venue_dir.mkdir(parents=True, exist_ok=True)
            (venue_dir / "20260724.json").write_text(
                json.dumps(existing_previous, ensure_ascii=False),
                encoding="utf-8",
            )
        with (
            patch.object(builder, "CONFIG_PATH", config_path),
            patch.object(builder, "ALL_VENUES", [("toda", "戸田")]),
            patch.object(builder, "build_payload", return_value=(deepcopy(morning), {"reason": "ok"})),
            patch.object(
                sys,
                "argv",
                [
                    "build_site_data.py",
                    "--date",
                    DATE,
                    "--source-root",
                    str(source_root),
                    "--data-root",
                    str(data_root),
                ],
            ),
        ):
            self.assertEqual(builder.main(), 0)
        manifest = json.loads((data_root / "manifest.json").read_text(encoding="utf-8"))
        return manifest, data_root

    def test_prediction_unavailable_still_publishes_all_race_domains_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, data_root = self.run_builder(Path(temporary), race_payload())
            venue = manifest["venues"][0]
            self.assertEqual(venue["venue"], "toda")
            self.assertTrue(venue["open"])
            self.assertTrue(venue["race_data_available"])
            self.assertFalse(venue["prediction_available"])
            self.assertEqual(venue["prediction_status"], "unavailable")
            self.assertEqual(venue["prediction_reason"], "prediction_payload_unavailable")
            document = json.loads((data_root / venue["dataPath"]).read_text(encoding="utf-8"))
            self.assertEqual(len(document["races"]), 12)
            self.assertEqual(document["preds"], {})
            self.assertNotEqual(document.get("engine"), "deterministic_baseline_v1")
            for race_no, race in enumerate(document["races"], 1):
                self.assertEqual(race["race_meta"]["race_no"], race_no)
                self.assertEqual(race["race_meta"]["date"], DATE)
                self.assertEqual(len(race["entries"]), 6)
                self.assertEqual(sorted(item["lane"] for item in race["entries"]), list(range(1, 7)))
                self.assertTrue(race["race_meta"]["deadline"])
                self.assertEqual(race["prediction"]["status"], "unavailable")
                self.assertIsNone(race["prediction"]["probabilities"])
                self.assertIsNone(race["prediction"]["sab"])
                self.assertIsNone(race["prediction"]["tickets"])

    def test_same_day_prediction_live_odds_result_and_setsukan_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            existing = race_payload(predictions=True)
            before = deepcopy(existing)
            manifest, data_root = self.run_builder(
                Path(temporary),
                race_payload(),
                existing_today=existing,
            )
            venue = manifest["venues"][0]
            self.assertTrue(venue["prediction_available"])
            after = json.loads((data_root / venue["dataPath"]).read_text(encoding="utf-8"))
            self.assertEqual(after["preds"], before["preds"])
            self.assertEqual(after["races"][0]["racers"][0]["season_runs"], before["races"][0]["racers"][0]["season_runs"])
            self.assertTrue(after["races"][0]["live"])
            self.assertTrue(after["races"][0]["odds"])
            self.assertEqual(after["races"][0]["result"]["status"], "ok")

    def test_previous_day_prediction_is_not_copied_to_today(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            previous = race_payload(date="2026-07-24", predictions=True)
            manifest, data_root = self.run_builder(
                Path(temporary),
                race_payload(),
                existing_previous=previous,
            )
            document = json.loads((data_root / manifest["venues"][0]["dataPath"]).read_text(encoding="utf-8"))
            self.assertEqual(document["date"], DATE)
            self.assertEqual(document["preds"], {})
            self.assertEqual(document["predictionStatus"], "unavailable")

    def test_non_running_venue_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest, data_root = self.run_builder(
                Path(temporary),
                None,
                open_venue=False,
            )
            venue = manifest["venues"][0]
            self.assertFalse(venue["open"])
            self.assertFalse(venue["race_data_available"])
            self.assertEqual(venue["dataPath"], "")
            self.assertFalse((data_root / "venues" / "toda" / "20260725.json").exists())


if __name__ == "__main__":
    unittest.main()
