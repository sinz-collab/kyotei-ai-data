from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE_DIR))

from tokoname_site_pipeline import (
    DEFAULT_MODEL_DIR,
    ENGINE_VERSION,
    apply_tokoname_predictions,
    build_engine_input,
    compare_results,
    load_live_documents,
    without_predictions,
)


DATE = "2026-07-30"


def racer(lane: int) -> dict:
    return {
        "lane": lane,
        "entry_course": lane,
        "actual_course": lane,
        "name": f"選手{lane}",
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
        "races": [
            {
                "race": race_no,
                "deadline": "10:00",
                "racers": [racer(lane) for lane in range(1, 7)],
                "setsukan": [{"lane": 1, "finish": "1着"}],
                "prediction": {"status": "existing", "marker": race_no},
                "live": {"keep": race_no},
                "odds": {"keep": race_no},
                "result": {"keep": race_no},
            }
            for race_no in range(1, 13)
        ],
    }


def write_live(root: Path, race_no: int, actual_entry: list[int] | None = None) -> None:
    race_dir = root / f"{race_no:02d}"
    race_dir.mkdir(parents=True)
    actual_entry = actual_entry or list(range(1, 7))
    common = {
        "complete": True,
        "status": "complete",
        "venue": "tokoname",
        "date": DATE,
        "race_no": race_no,
    }
    direct = {
        **common,
        "data": {
            "entry_changed": actual_entry != list(range(1, 7)),
            "actual_entry": actual_entry,
            "racers": [
                {"lane": lane, "player_id": 5000 + lane}
                for lane in range(1, 7)
            ],
        },
    }
    exhibition = {
        **common,
        "data": {
            "entries": [
                {
                    "lane": lane,
                    "exhibition_course": actual_entry.index(lane) + 1,
                    "exhibition_time": 6.70 + lane / 100,
                    "start_time": lane / 100,
                }
                for lane in range(1, 7)
            ]
        },
    }
    original = {
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
    }
    for name, payload in (
        ("direct.json", direct),
        ("exhibition.json", exhibition),
        ("original_exhibition.json", original),
    ):
        (race_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    odds = {
        f"{first}-{second}-{third}": 10.0
        for first in range(1, 7)
        for second in range(1, 7)
        for third in range(1, 7)
        if len({first, second, third}) == 3
    }
    (race_dir / "odds.json").write_text(
        json.dumps({**common, "data": {"odds": odds}}),
        encoding="utf-8",
    )


def fake_predict(payload: dict, model_dir: Path) -> dict:
    assert model_dir == DEFAULT_MODEL_DIR
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
        "data_flags": {"entry_change": payload["direct"]["data"]["entry_changed"]},
    }


class TokonameSitePipelineTests(unittest.TestCase):
    def test_success_changes_only_prediction_and_uses_actual_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live_root = Path(directory)
            write_live(live_root, 1, [1, 3, 2, 4, 5, 6])
            source = morning()
            before = deepcopy(source)
            captured = {}

            def predictor(payload: dict, model_dir: Path) -> dict:
                captured["payload"] = deepcopy(payload)
                return fake_predict(payload, model_dir)

            updated, reports = apply_tokoname_predictions(
                source,
                live_root=live_root,
                race_numbers=[1],
                predictor=predictor,
            )

            self.assertEqual(reports[0]["status"], "updated")
            self.assertEqual(without_predictions(updated), without_predictions(before))
            self.assertEqual(source, before)
            courses = {
                row["lane"]: row["actual_course"]
                for row in captured["payload"]["race"]["racers"]
            }
            self.assertEqual(courses, {1: 1, 2: 3, 3: 2, 4: 4, 5: 5, 6: 6})
            prediction = updated["races"][0]["prediction"]
            self.assertEqual(prediction["engine_version"], ENGINE_VERSION)
            self.assertTrue(prediction["original_exhibition_available"])
            self.assertIn(
                "original_exhibition", prediction["engine_run"]["inputs"]
            )
            self.assertIn("original_exhibition", captured["payload"])
            self.assertFalse(prediction["data_flags"]["odds_used_for_probability"])
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(
                    sum(prediction["probabilities"][key].values()),
                    100.0,
                    places=1,
                )
            self.assertEqual(
                {
                    key: len(prediction["tickets"][key])
                    for key in ("main", "deviation", "upset")
                },
                {"main": 6, "deviation": 2, "upset": 2},
            )
            self.assertEqual(updated["races"][1]["prediction"], before["races"][1]["prediction"])

    def test_failure_preserves_existing_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live_root = Path(directory)
            write_live(live_root, 1)
            source = morning()

            def failing_predictor(payload: dict, model_dir: Path) -> dict:
                raise RuntimeError("model failed")

            updated, reports = apply_tokoname_predictions(
                source,
                live_root=live_root,
                race_numbers=[1],
                predictor=failing_predictor,
            )
            self.assertEqual(reports[0]["status"], "preserved")
            self.assertEqual(updated, source)

    def test_result_is_loaded_only_for_post_prediction_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live_root = Path(directory)
            write_live(live_root, 1)
            result = {
                "data": {"order": ["1", "2", "3"]},
                "complete": True,
                "status": "complete",
                "venue": "tokoname",
                "date": DATE,
                "race_no": 1,
            }
            (live_root / "01" / "result.json").write_text(
                json.dumps(result),
                encoding="utf-8",
            )
            source = morning()
            updated, reports = apply_tokoname_predictions(
                source,
                live_root=live_root,
                race_numbers=[1],
                predictor=fake_predict,
            )
            comparison = compare_results(reports, updated, live_root)
            self.assertEqual(
                comparison,
                [{"race": 1, "result": "1-2-3", "ticket_hit": True}],
            )

    def test_live_loader_reads_odds_but_never_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live_root = Path(directory)
            write_live(live_root, 1)
            documents = load_live_documents(live_root / "01", DATE, 1)
            self.assertEqual(
                set(documents),
                {"direct", "exhibition", "original_exhibition", "odds"},
            )

    def test_pending_original_exhibition_is_optional_and_not_passed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live_root = Path(directory)
            write_live(live_root, 10)
            original_path = live_root / "10" / "original_exhibition.json"
            original = json.loads(original_path.read_text(encoding="utf-8"))
            original.update({"status": "pending", "complete": False})
            original["data"]["entries"] = []
            original_path.write_text(json.dumps(original), encoding="utf-8")
            captured = {}

            def predictor(payload: dict, model_dir: Path) -> dict:
                captured["payload"] = deepcopy(payload)
                return fake_predict(payload, model_dir)

            updated, reports = apply_tokoname_predictions(
                morning(),
                live_root=live_root,
                race_numbers=[10],
                predictor=predictor,
            )

            prediction = updated["races"][9]["prediction"]
            self.assertNotIn("original_exhibition", captured["payload"])
            self.assertEqual(prediction["prediction_phase"], "final")
            self.assertTrue(prediction["engine_recalculated_after_exhibition"])
            self.assertTrue(prediction["engine_run"]["completed"])
            self.assertFalse(prediction["original_exhibition_available"])
            self.assertFalse(
                prediction["data_flags"]["original_exhibition_available"]
            )
            self.assertNotIn(
                "original_exhibition", prediction["engine_run"]["inputs"]
            )
            self.assertFalse(prediction["data_flags"]["odds_used_for_probability"])
            self.assertFalse(reports[0]["original_exhibition_available"])
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(
                    sum(prediction["probabilities"][key].values()),
                    100.0,
                    places=1,
                )


if __name__ == "__main__":
    unittest.main()
