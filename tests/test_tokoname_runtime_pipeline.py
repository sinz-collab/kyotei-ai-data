from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "engines" / "tokoname_v1"))

import build_site_data
from live_fetch_once import stage_tokoname_results
from stage_tokoname_predictions import stage_tokoname_predictions
from tokoname_site_pipeline import DEFAULT_MODEL_DIR, without_predictions


DATE = "2026-07-30"


def racer(lane: int) -> dict:
    return {
        "lane": lane,
        "entry_course": lane,
        "actual_course": lane,
        "name": f"Racer {lane}",
        "class": "A2" if lane == 1 else "B1",
        "motor_no": str(lane),
        "boat_no": str(lane),
        "local_3": "50.0",
        "nat_3": "50.0",
        "motor_3": "50.0",
        "boat_3": "50.0",
    }


def morning() -> dict:
    return {
        "venueId": "tokoname",
        "venue": "常滑",
        "date": DATE,
        "eventDay": 1,
        "tide": {},
        "engine": "",
        "preds": {},
        "races": [
            {
                "race": race_no,
                "deadline": "10:00",
                "racers": [racer(lane) for lane in range(1, 7)],
                "setsukan": [{"lane": 1, "finish": "1着"}],
                "live": {"keep": race_no},
                "odds": {"keep": race_no},
                "result": {"keep": race_no},
            }
            for race_no in range(1, 13)
        ],
    }


def write_live(
    live_root: Path,
    race_no: int,
    *,
    filenames: tuple[str, ...] = (
        "direct.json",
        "exhibition.json",
        "original_exhibition.json",
        "odds.json",
    ),
) -> None:
    race_dir = live_root / DATE / "tokoname" / f"{race_no:02d}"
    race_dir.mkdir(parents=True, exist_ok=True)
    common = {
        "complete": True,
        "status": "complete",
        "venue": "tokoname",
        "date": DATE,
        "race_no": race_no,
    }
    documents = {
        "direct.json": {
            **common,
            "data": {
                "entry_changed": False,
                "actual_entry": list(range(1, 7)),
                "racers": [
                    {"lane": lane, "player_id": 5000 + lane}
                    for lane in range(1, 7)
                ],
            },
        },
        "exhibition.json": {
            **common,
            "data": {
                "entries": [
                    {
                        "lane": lane,
                        "exhibition_course": lane,
                        "exhibition_time": 6.70 + lane / 100,
                        "start_time": lane / 100,
                    }
                    for lane in range(1, 7)
                ]
            },
        },
        "original_exhibition.json": {
            **common,
            "data": {
                "entries": [
                    {
                        "lane": lane,
                        "lap_time": 37.0 + lane / 10,
                        "turn_time": 5.0 + lane / 10,
                        "straight_time": 7.0 + lane / 10,
                    }
                    for lane in range(1, 7)
                ]
            },
        },
        "odds.json": {
            **common,
            "data": {
                "odds": {
                    f"{first}-{second}-{third}": 10.0
                    for first in range(1, 7)
                    for second in range(1, 7)
                    for third in range(1, 7)
                    if len({first, second, third}) == 3
                }
            },
        },
    }
    for filename in filenames:
        (race_dir / filename).write_text(
            json.dumps(documents[filename], ensure_ascii=False),
            encoding="utf-8",
        )


def fake_predict(payload: dict, model_dir: Path) -> dict:
    if model_dir != DEFAULT_MODEL_DIR:
        raise AssertionError("unexpected model directory")
    probabilities = [
        {
            "lane": lane,
            "win": [40, 20, 15, 10, 8, 7][lane - 1],
            "second": [10, 30, 20, 15, 15, 10][lane - 1],
            "third": [10, 15, 25, 20, 15, 15][lane - 1],
        }
        for lane in range(1, 7)
    ]
    combinations = [
        "1-2-3",
        "1-2-4",
        "1-3-2",
        "1-3-4",
        "1-4-2",
        "1-4-3",
        "2-1-3",
        "3-1-2",
        "4-1-2",
        "5-1-2",
    ]
    ticket = lambda combo, category: {
        "combination": combo,
        "score_pct": 1.0,
        "category": category,
    }
    return {
        "engine": "tokoname_engine_v1.6",
        "stage": "preliminary" if payload.get("preliminary") else "final",
        "probabilities": probabilities,
        "sab": {"grade": "A", "score": 70},
        "scenario": {"head": 1},
        "tickets": {
            "main": [ticket(combo, "main") for combo in combinations[:6]],
            "deviation": [
                ticket(combo, "deviation") for combo in combinations[6:8]
            ],
            "upset": [ticket(combo, "upset") for combo in combinations[8:]],
        },
        "data_flags": {"entry_change": False},
    }


def file_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class TokonameBuildPreservationTests(unittest.TestCase):
    def test_tokoname_prediction_is_not_overwritten(self) -> None:
        payload = morning()
        generated = build_site_data.attach_independent_race_domains(
            deepcopy(payload),
            "tokoname",
            False,
            "prediction_payload_unavailable",
        )
        generated["races"][0]["prediction"] = self._prediction()
        before = deepcopy(generated["races"][0]["prediction"])
        rebuilt = build_site_data.attach_independent_race_domains(
            generated,
            "tokoname",
            False,
            "prediction_payload_unavailable",
        )
        self.assertEqual(rebuilt["races"][0]["prediction"], before)

    def test_other_venue_legacy_behavior_is_unchanged(self) -> None:
        payload = morning()
        payload["venueId"] = "toda"
        payload["races"][0]["prediction"] = self._prediction()
        rebuilt = build_site_data.attach_independent_race_domains(
            payload,
            "toda",
            False,
            "prediction_payload_unavailable",
        )
        self.assertEqual(rebuilt["races"][0]["prediction"]["status"], "unavailable")

    @staticmethod
    def _prediction() -> dict:
        document = morning()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            morning_root = root / "runtime" / "morning"
            live_root = root / "data" / "live"
            output_root = root / "runtime" / "predictions"
            path = morning_root / "venues" / "tokoname" / "20260730.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            write_live(live_root, 1)
            stage_tokoname_predictions(
                DATE,
                morning_root=morning_root,
                live_root=live_root,
                output_root=output_root,
                race_numbers=[1],
                predictor=fake_predict,
            )
            staged = json.loads(
                (output_root / "venues" / "tokoname" / "20260730.json").read_text(
                    encoding="utf-8"
                )
            )
            return staged["races"][0]["prediction"]


class TokonameRuntimeStagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.morning_root = self.root / "runtime" / "morning"
        self.live_root = self.root / "data" / "live"
        self.output_root = self.root / "runtime" / "predictions"
        self.morning_path = (
            self.morning_root / "venues" / "tokoname" / "20260730.json"
        )
        self.morning_path.parent.mkdir(parents=True)
        self.source = morning()
        self.morning_path.write_text(
            json.dumps(self.source, ensure_ascii=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stage(self, **kwargs) -> dict:
        return stage_tokoname_predictions(
            DATE,
            morning_root=self.morning_root,
            live_root=self.live_root,
            output_root=self.output_root,
            predictor=kwargs.pop("predictor", fake_predict),
            **kwargs,
        )

    def test_incomplete_live_inputs_only_generate_preliminary(self) -> None:
        write_live(self.live_root, 1, filenames=("direct.json", "exhibition.json"))
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(payload)
            return fake_predict(payload, model_dir)

        before_data = file_snapshot(self.root / "data")
        result = self.stage(race_numbers=[1], predictor=predictor)
        self.assertEqual(result["status"], "preliminary")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["preliminary"])
        staged = json.loads(
            (
                self.output_root / "venues" / "tokoname" / "20260730.json"
            ).read_text(encoding="utf-8")
        )
        prediction = staged["races"][0]["prediction"]
        self.assertEqual(prediction["prediction_phase"], "preliminary")
        self.assertFalse(prediction["engine_recalculated_after_exhibition"])
        self.assertEqual(file_snapshot(self.root / "data"), before_data)

    def test_only_complete_race_is_generated_and_dated_matches_latest(self) -> None:
        write_live(self.live_root, 1)
        write_live(self.live_root, 2, filenames=("direct.json", "exhibition.json"))
        original_morning = self.morning_path.read_bytes()
        before_data = file_snapshot(self.root / "data")
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(int(payload["race"]["race"]))
            return fake_predict(payload, model_dir)

        result = self.stage(race_numbers=[1, 2], predictor=predictor)
        dated = self.output_root / "venues" / "tokoname" / "20260730.json"
        latest = self.output_root / "venues" / "tokoname" / "latest.json"
        staged = json.loads(dated.read_text(encoding="utf-8"))

        self.assertEqual(result["ready_races"], [1])
        self.assertEqual(result["updated_races"], [1])
        self.assertEqual(result["engine_invoked_races"], [1])
        self.assertEqual(calls, [1])
        self.assertEqual(dated.read_bytes(), latest.read_bytes())
        self.assertEqual(
            without_predictions(staged),
            without_predictions(self.source),
        )
        self.assertTrue(staged["races"][0]["prediction"]["input_hash"])
        prediction = staged["races"][0]["prediction"]
        self.assertEqual(prediction["prediction_phase"], "final")
        self.assertTrue(prediction["engine_recalculated_after_exhibition"])
        self.assertEqual(
            prediction["engine_run"]["source_engine"],
            "tokoname_engine_v1.6",
        )
        self.assertNotIn("prediction", staged["races"][1])
        self.assertEqual(self.morning_path.read_bytes(), original_morning)
        self.assertEqual(file_snapshot(self.root / "data"), before_data)

    def test_failure_preserves_existing_prediction(self) -> None:
        write_live(self.live_root, 1)
        first = self.stage(race_numbers=[1])
        self.assertTrue(first["written"])
        dated = self.output_root / "venues" / "tokoname" / "20260730.json"
        before = dated.read_bytes()
        direct_path = self.live_root / DATE / "tokoname" / "01" / "direct.json"
        direct = json.loads(direct_path.read_text(encoding="utf-8"))
        direct["data"]["entry_changed"] = True
        direct["data"]["actual_entry"] = [2, 1, 3, 4, 5, 6]
        direct_path.write_text(json.dumps(direct), encoding="utf-8")

        def failing_predictor(payload: dict, model_dir: Path) -> dict:
            raise RuntimeError("model failed")

        result = self.stage(race_numbers=[1], predictor=failing_predictor)
        self.assertEqual(result["status"], "preserved")
        self.assertEqual(result["preserved_races"], [1])
        self.assertFalse(result["written"])
        self.assertEqual(dated.read_bytes(), before)
        self.assertEqual(
            (self.output_root / "venues" / "tokoname" / "latest.json").read_bytes(),
            before,
        )

    def test_same_input_hash_skips_model_and_write(self) -> None:
        write_live(self.live_root, 1)
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(int(payload["race"]["race"]))
            return fake_predict(payload, model_dir)

        first = self.stage(race_numbers=[1], predictor=predictor)
        dated = self.output_root / "venues" / "tokoname" / "20260730.json"
        before = dated.read_bytes()
        second = self.stage(race_numbers=[1], predictor=predictor)

        self.assertEqual(first["updated_races"], [1])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["unchanged_races"], [1])
        self.assertEqual(calls, [1])
        self.assertEqual(dated.read_bytes(), before)

    def test_exhibition_change_recalculates_only_changed_race(self) -> None:
        write_live(self.live_root, 1)
        write_live(self.live_root, 2)
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(int(payload["race"]["race"]))
            return fake_predict(payload, model_dir)

        self.stage(race_numbers=[1, 2], predictor=predictor)
        exhibition_path = (
            self.live_root / DATE / "tokoname" / "02" / "exhibition.json"
        )
        exhibition = json.loads(exhibition_path.read_text(encoding="utf-8"))
        exhibition["data"]["entries"][0]["exhibition_time"] = 6.55
        exhibition_path.write_text(json.dumps(exhibition), encoding="utf-8")
        result = self.stage(race_numbers=[1, 2], predictor=predictor)

        self.assertEqual(result["updated_races"], [2])
        self.assertEqual(result["unchanged_races"], [1])
        self.assertEqual(calls, [1, 2, 2])

    def test_odds_are_passed_for_display_but_do_not_retrigger_engine(self) -> None:
        write_live(self.live_root, 1)
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(deepcopy(payload))
            return fake_predict(payload, model_dir)

        first = self.stage(race_numbers=[1], predictor=predictor)
        self.assertEqual(first["engine_invoked_races"], [1])
        self.assertEqual(len(calls[0]["odds"]["data"]["odds"]), 120)

        odds_path = self.live_root / DATE / "tokoname" / "01" / "odds.json"
        odds = json.loads(odds_path.read_text(encoding="utf-8"))
        odds["data"]["odds"]["1-2-3"] = 99.9
        odds_path.write_text(json.dumps(odds), encoding="utf-8")
        second = self.stage(race_numbers=[1], predictor=predictor)

        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["unchanged_races"], [1])
        self.assertEqual(len(calls), 1)

    def test_result_complete_stops_recalculation_and_preserves_prediction(self) -> None:
        write_live(self.live_root, 1)
        first = self.stage(race_numbers=[1])
        self.assertEqual(first["updated_races"], [1])
        dated = self.output_root / "venues" / "tokoname" / "20260730.json"
        before = dated.read_bytes()
        result_path = self.live_root / DATE / "tokoname" / "01" / "result.json"
        result_path.write_text(
            json.dumps(
                {
                    "date": DATE,
                    "venue": "tokoname",
                    "race_no": 1,
                    "status": "complete",
                    "complete": True,
                    "data": {"order": [1, 2, 3]},
                }
            ),
            encoding="utf-8",
        )
        calls = []

        def predictor(payload: dict, model_dir: Path) -> dict:
            calls.append(payload)
            return fake_predict(payload, model_dir)

        result = self.stage(race_numbers=[1], predictor=predictor)

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["result_complete_races"], [1])
        self.assertEqual(calls, [])
        self.assertEqual(dated.read_bytes(), before)

    def test_non_tokoname_live_result_does_not_start_staging(self) -> None:
        class Logger:
            def error(self, *args, **kwargs) -> None:
                raise AssertionError("unexpected staging error")

        result = stage_tokoname_results(
            {"morning_data_root": str(self.morning_root)},
            DATE,
            self.live_root,
            [
                {
                    "target": {"venue": "toda", "race_no": 1},
                    "items": {
                        item: {"complete": True, "status": "complete"}
                        for item in (
                            "direct",
                            "exhibition",
                            "original_exhibition",
                        )
                    },
                }
            ],
            Logger(),
        )
        self.assertIsNone(result)
        self.assertFalse(self.output_root.exists())

    def test_live_hook_runs_only_when_all_four_inputs_are_complete(self) -> None:
        class Logger:
            def error(self, *args, **kwargs) -> None:
                raise AssertionError("unexpected staging error")

        base = {
            "target": {"venue": "tokoname", "race_no": 1},
            "items": {
                item: {"complete": True, "status": "complete"}
                for item in (
                    "direct",
                    "exhibition",
                    "original_exhibition",
                    "odds",
                )
            },
        }
        incomplete = deepcopy(base)
        incomplete["items"]["original_exhibition"]["complete"] = False
        incomplete["items"]["original_exhibition"]["status"] = "partial"
        with patch(
            "stage_tokoname_predictions.stage_tokoname_predictions"
        ) as staging:
            self.assertIsNone(
                stage_tokoname_results(
                    {"morning_data_root": str(self.morning_root)},
                    DATE,
                    self.live_root,
                    [incomplete],
                    Logger(),
                )
            )
            staging.assert_not_called()

            staging.return_value = {"status": "updated", "written": True}
            result = stage_tokoname_results(
                {"morning_data_root": str(self.morning_root)},
                DATE,
                self.live_root,
                [base],
                Logger(),
            )
            self.assertEqual(result, staging.return_value)
            staging.assert_called_once()


if __name__ == "__main__":
    unittest.main()
