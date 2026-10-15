from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

AUTOMATION = Path(__file__).resolve().parents[1] / "automation"
sys.path.insert(0, str(AUTOMATION))

from run_wakamatsu_v3_staging import (
    build_staging_payload,
    live_model_row,
    morning_provisional_probability,
    run_staging,
)
from publish_wakamatsu_v3 import feature_enabled, mark_live_v3_unavailable, publish_v3


JST = ZoneInfo("Asia/Tokyo")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def prediction(phase: str) -> dict:
    final = phase == "final"
    return {
        "phase": phase,
        "win": {str(lane): value for lane, value in enumerate((38.87, 18, 14, 12, 10, 7.13), 1)},
        "second": {str(lane): 100 / 6 for lane in range(1, 7)},
        "third": {str(lane): 100 / 6 for lane in range(1, 7)},
        "tickets": [{"combo": f"1-2-{lane}", "probability": lane} for lane in range(1, 7)]
        + [{"combo": f"2-1-{lane}", "probability": lane} for lane in range(1, 5)],
        "aiUpset": [{"combo": f"2-3-{lane}"} for lane in range(1, 11)],
        "flyPrediction": {"tickets": [{"combo": f"2-1-{lane}"} for lane in range(1, 11)]},
        "raceContext": {"wind_speed": 4.0, "tide_phase": "falling"},
        "diagnostics": {
            "oddsUsedForPrediction": False,
            "fullReflection": {
                "baseAudit": {
                    "1": {"win": {"player_course": {"observed": 0.125, "starts": 16}}}
                }
            },
        },
        "finalPredictionStatus": "complete" if final else "waiting_live_data",
    }


def race(complete: bool = True) -> dict:
    pre = prediction("pre")
    final = prediction("final")
    racers = [
        {"lane": lane, "avg_st": 0.16 + lane / 100, "local_win": 4.96 if lane == 1 else 5, "boaters_escape_rate": 27.3}
        for lane in range(1, 7)
    ]
    originals = [
        {"lane": lane, "lap_time": 37 + lane / 10, "turn_time": 5.5 + lane / 20, "straight_time": 6 + lane / 100}
        for lane in range(1, 7)
    ]
    value = {
        "race": 1,
        "deadline": "15:25",
        "racers": racers,
        "direct": {"actual_entry": [1, 2, 3, 4, 5, 6]} if complete else {},
        "exhibition": {"entries": [{"lane": lane} for lane in range(1, 7)]} if complete else {},
        "original_exhibition": {"entries": originals} if complete else {},
        "predictionPre": pre,
        "predictionFinal": final,
        "prediction": deepcopy(final if complete else pre),
        "odds": {"3t": {"1-2-3": 9.9}},
        "result": {"finish": [6, 5, 4]},
    }
    return value


def tide(status: str = "ok") -> dict:
    if status != "ok":
        return {"status": "unavailable", "reason": "fetch_failed:TimeoutError", "fresh": False}
    return {
        "status": "ok",
        "reason": "validated_fresh_fetch",
        "targetDate": "2026-10-14",
        "sourceDate": "2026-10-14",
        "fetchedAt": datetime(2026, 10, 14, 14, 50, tzinfo=JST).isoformat(),
        "fresh": True,
        "events": [
            {"date": "2026-10-14", "type": "low", "time": "05:39", "level": 35},
            {"date": "2026-10-14", "type": "high", "time": "11:59", "level": 125},
            {"date": "2026-10-14", "type": "low", "time": "17:19", "level": 60},
            {"date": "2026-10-14", "type": "high", "time": "23:19", "level": 153},
            {"date": "2026-10-15", "type": "low", "time": "06:15", "level": 32},
            {"date": "2026-10-15", "type": "high", "time": "12:33", "level": 130},
        ],
    }


class WakamatsuV3StagingTest(unittest.TestCase):
    def payload(self, complete: bool = True) -> dict:
        return {
            "venueId": "wakamatsu",
            "venue": "若松",
            "date": "2026-10-14",
            "eventType": "women",
            "races": [race(complete)],
        }

    def test_adapter_uses_live_fields_and_never_result_or_odds(self):
        source = self.payload()["races"][0]
        before = live_model_row(source)
        source["result"] = {"finish": [1, 2, 3], "secret": 999}
        source["odds"] = {"3t": {"1-2-3": 9999}}
        self.assertEqual(before, live_model_row(source))
        self.assertEqual(before["boat1_live_win_pct"], 38.87)
        self.assertEqual(before["boat1_player_course_win_rate_pct"], 12.5)
        self.assertEqual(before["boat1_player_course_starts"], 16)

    def test_morning_complement_switches_to_available_live_v3_without_ticket_changes(self):
        source = self.payload()
        source["races"][0]["predictionFinal"]["flyPrediction"]["source"] = "live_pending_v3"
        tickets_before = deepcopy(source["races"][0]["predictionFinal"]["tickets"])
        fly_before = deepcopy(source["races"][0]["predictionFinal"]["flyPrediction"])
        staged, report = build_staging_payload(source, "2026-10-14", tide())
        staged_race = staged["races"][0]
        self.assertAlmostEqual(staged_race["predictionPre"]["lane1FlyProbability"], 61.13)
        self.assertEqual(
            staged_race["predictionPre"]["lane1FlyProbabilitySource"],
            "morning_provisional",
        )
        self.assertTrue(staged_race["predictionPre"]["lane1FlyProbabilityIsProvisional"])
        self.assertNotEqual(
            staged_race["predictionPre"]["lane1FlyProbabilityModel"],
            staged_race["predictionFinal"]["lane1FlyProbabilityModel"],
        )
        self.assertIsInstance(staged_race["predictionFinal"]["lane1FlyProbability"], float)
        self.assertEqual(staged_race["predictionFinal"]["lane1FlyProbabilitySource"], "live")
        self.assertEqual(staged_race["prediction"]["lane1FlyProbabilitySource"], "live")
        self.assertFalse(staged_race["predictionFinal"]["lane1FlyProbabilityIsProvisional"])
        self.assertEqual(staged_race["predictionFinal"]["tickets"], tickets_before)
        self.assertEqual(
            staged_race["predictionFinal"]["flyPrediction"],
            {**fly_before, "source": "live"},
        )
        self.assertEqual(report["available"], 1)
        self.assertEqual(report["unavailable"], 0)
        self.assertEqual(report["morningAvailable"], 1)

    def test_tide_failure_only_nulls_v3(self):
        source = self.payload()
        source["races"][0]["predictionFinal"]["flyPrediction"]["source"] = "live_pending_v3"
        final_before = deepcopy(source["races"][0]["predictionFinal"])
        staged, report = build_staging_payload(source, "2026-10-14", tide("failed"))
        final = staged["races"][0]["predictionFinal"]
        self.assertIsNone(final["lane1FlyProbability"])
        self.assertEqual(final["lane1FlyProbabilitySource"], "live")
        self.assertEqual(final["lane1FlyProbabilityReason"], "fetch_failed:TimeoutError")
        self.assertEqual(final["flyPrediction"]["source"], "live")
        self.assertEqual(final["win"], final_before["win"])
        self.assertEqual(final["tickets"], final_before["tickets"])
        self.assertEqual(report["unavailable"], 1)
        self.assertAlmostEqual(report["races"][0]["morningProvisionalFlyProbability"], 61.13)
        self.assertEqual(staged["races"][0]["prediction"]["lane1FlyProbability"], None)
        self.assertEqual(staged["races"][0]["prediction"]["lane1FlyProbabilityDisplayStage"], "live")

    def test_incomplete_live_is_null_and_not_a_legacy_fallback(self):
        staged, report = build_staging_payload(self.payload(False), "2026-10-14", tide())
        final = staged["races"][0]["predictionFinal"]
        self.assertIsNone(final["lane1FlyProbability"])
        self.assertEqual(final["lane1FlyProbabilitySource"], "live")
        self.assertEqual(final["lane1FlyProbabilityReason"], "live_exhibition_inputs_incomplete")
        self.assertEqual(report["available"], 0)
        self.assertAlmostEqual(staged["races"][0]["prediction"]["lane1FlyProbability"], 61.13)
        self.assertEqual(
            staged["races"][0]["prediction"]["lane1FlyProbabilitySource"],
            "morning_provisional",
        )
        self.assertEqual(staged["races"][0]["prediction"]["lane1FlyProbabilityDisplayStage"], "morning")

    def test_live_phase_with_missing_exhibition_is_null_not_morning_fallback(self):
        source = self.payload(False)
        source["races"][0]["prediction"] = deepcopy(source["races"][0]["predictionFinal"])
        staged, report = build_staging_payload(source, "2026-10-14", tide())
        active = staged["races"][0]["prediction"]
        self.assertIsNone(active["lane1FlyProbability"])
        self.assertEqual(active["lane1FlyProbabilitySource"], "live")
        self.assertEqual(active["lane1FlyProbabilityReason"], "live_exhibition_inputs_incomplete")
        self.assertAlmostEqual(report["races"][0]["morningProvisionalFlyProbability"], 61.13)

    def test_invalid_morning_lane1_win_is_unavailable_and_not_v3(self):
        for invalid in (None, "", "NaN", -1, 101):
            with self.subTest(invalid=invalid):
                source = self.payload(False)
                source["races"][0]["predictionPre"]["win"]["1"] = invalid
                staged, report = build_staging_payload(source, "2026-10-14", tide())
                pre = staged["races"][0]["predictionPre"]
                self.assertIsNone(pre["lane1FlyProbability"])
                self.assertTrue(pre["lane1FlyProbabilityIsProvisional"])
                self.assertNotEqual(pre["lane1FlyProbabilityModel"], "wakamatsu_v3_lane1_tide_frozen")
                self.assertEqual(report["morningUnavailable"], 1)

    def test_morning_formula_reads_only_pre_prediction(self):
        source = self.payload()["races"][0]
        before = morning_provisional_probability(source["predictionPre"])
        source["direct"] = {"secret": 999}
        source["exhibition"] = {"secret": 999}
        source["original_exhibition"] = {"secret": 999}
        source["odds"] = {"secret": 999}
        source["result"] = {"secret": 999}
        self.assertEqual(before, morning_provisional_probability(source["predictionPre"]))

    def test_run_writes_only_staging_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public = root / "public"
            output = root / "staging"
            path = public / "venues" / "wakamatsu" / "20261014.json"
            path.parent.mkdir(parents=True)
            original = json.dumps(self.payload(), ensure_ascii=False)
            path.write_text(original, encoding="utf-8")
            report = run_staging("2026-10-14", public, output, tide_provider=lambda _: tide())
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertTrue((output / "venues" / "wakamatsu" / "20261014.json").is_file())
            self.assertFalse(report["productionWritten"])

    def test_production_publish_has_no_staging_envelope_and_preserves_tickets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "venues" / "wakamatsu" / "20261014.json"
            latest = path.with_name("latest.json")
            original = self.payload()
            original["races"][0]["predictionFinal"]["flyPrediction"]["source"] = "live_pending_v3"
            write_json(path, original)
            write_json(latest, original)
            tickets_before = deepcopy(original["races"][0]["predictionFinal"]["tickets"])
            fly_before = deepcopy(original["races"][0]["predictionFinal"]["flyPrediction"])
            with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "true"}):
                report = publish_v3("2026-10-14", root, tide_provider=lambda _: tide())
            published = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("staging", published)
            self.assertTrue(report["productionWritten"])
            self.assertTrue(report["latestUpdated"])
            self.assertEqual(published, json.loads(latest.read_text(encoding="utf-8")))
            self.assertEqual(published["races"][0]["predictionFinal"]["tickets"], tickets_before)
            self.assertEqual(
                published["races"][0]["predictionFinal"]["flyPrediction"],
                {**fly_before, "source": "live"},
            )
            self.assertEqual(published["races"][0]["prediction"]["lane1FlyProbabilitySource"], "live")
            self.assertNotIn("live_pending_v3", path.read_text(encoding="utf-8"))

            with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "true"}):
                publish_v3("2026-10-14", root, tide_provider=lambda _: tide())
            republished = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                republished["races"][0]["predictionFinal"]["flyPrediction"]["source"],
                "live",
            )
            self.assertEqual(republished["races"][0]["predictionFinal"]["tickets"], tickets_before)

    def test_production_publish_does_not_touch_other_venues(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            wakamatsu = root / "venues" / "wakamatsu" / "20261014.json"
            write_json(wakamatsu, self.payload())
            write_json(wakamatsu.with_name("latest.json"), self.payload())
            other = root / "venues" / "omura" / "20261014.json"
            other_payload = {"venueId": "omura", "date": "2026-10-14", "races": [{"race": 1}]}
            write_json(other, other_payload)
            before = other.read_bytes()

            with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "true"}):
                publish_v3("2026-10-14", root, tide_provider=lambda _: tide())

            self.assertEqual(other.read_bytes(), before)

    def test_feature_flag_disables_only_live_v3_probability(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "venues" / "wakamatsu" / "20261014.json"
            latest = path.with_name("latest.json")
            original = self.payload()
            tickets_before = deepcopy(original["races"][0]["predictionFinal"]["tickets"])
            write_json(path, original)
            write_json(latest, original)
            with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "false"}):
                report = publish_v3("2026-10-14", root, tide_provider=lambda _: tide())
            published = json.loads(path.read_text(encoding="utf-8"))
            active = published["races"][0]["prediction"]
            self.assertIsNone(active["lane1FlyProbability"])
            self.assertEqual(active["lane1FlyProbabilityReason"], "v3_feature_disabled")
            self.assertEqual(active["tickets"], tickets_before)
            self.assertEqual(report["affectedLiveRaces"], 1)

    def test_feature_flag_environment_override_is_strict(self):
        with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "off"}):
            self.assertFalse(feature_enabled())
        with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "on"}):
            self.assertTrue(feature_enabled())
        with patch.dict("os.environ", {"WAKAMATSU_V3_ENABLED": "maybe"}):
            with self.assertRaisesRegex(RuntimeError, "feature_flag_invalid"):
                feature_enabled()

    def test_emergency_unavailable_does_not_change_normal_or_fly_tickets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "venues" / "wakamatsu" / "20261014.json"
            latest = path.with_name("latest.json")
            original = self.payload()
            original["races"][0]["predictionFinal"]["flyPrediction"]["source"] = "live_pending_v3"
            normal = deepcopy(original["races"][0]["predictionFinal"]["tickets"])
            fly = deepcopy(original["races"][0]["predictionFinal"]["flyPrediction"])
            write_json(path, original)
            write_json(latest, original)
            mark_live_v3_unavailable("2026-10-14", root, "v3_pipeline_error:Test")
            published = json.loads(path.read_text(encoding="utf-8"))
            final = published["races"][0]["predictionFinal"]
            self.assertIsNone(final["lane1FlyProbability"])
            self.assertEqual(final["lane1FlyProbabilitySource"], "live")
            self.assertEqual(final["tickets"], normal)
            self.assertEqual(final["flyPrediction"], {**fly, "source": "live"})
            self.assertNotIn("live_pending_v3", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
