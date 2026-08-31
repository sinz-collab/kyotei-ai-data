from __future__ import annotations

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import run_biwako_v1_2 as biwako


DATE = "2026-08-17"


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE race_history ("
        "date TEXT, race_no INTEGER, reg_no TEXT, racer_name TEXT, "
        "actual_course INTEGER, finish INTEGER, st REAL, kimarite TEXT)"
    )
    connection.execute(
        "CREATE TABLE motor_recent ("
        "motor_no INTEGER, top2_rate_total REAL, top3_rate_total REAL, "
        "win_rate_recent10 REAL, second_rate_recent10 REAL, "
        "top3_rate_recent10 REAL, starts_recent10 INTEGER, date TEXT)"
    )
    connection.commit()
    connection.close()


def payload() -> dict:
    races = []
    for race_no in range(1, 13):
        racers = []
        for lane in range(1, 7):
            racers.append(
                {
                    "lane": lane,
                    "actual_course": lane,
                    "player_id": 4000 + lane,
                    "name": f"Racer {lane}",
                    "nat_win": 7.0 - lane * 0.4,
                    "local_win": 6.8 - lane * 0.35,
                    "motor_no": 10 + lane,
                    "motor_2": 40 - lane,
                    "motor_3": 55 - lane,
                    "season_runs": [
                        {"course": lane, "finish": 2 + lane % 3, "st": 0.12 + lane * 0.01}
                    ],
                }
            )
        races.append(
            {
                "race": race_no,
                "deadline": "10:00",
                "eventDay": 3,
                "eventDayLabel": "3日目",
                "racers": racers,
            }
        )
    return {
        "date": DATE,
        "venueId": "biwako",
        "venue": "びわこ",
        "eventDay": 3,
        "eventDayLabel": "3日目",
        "races": races,
        "preds": {},
    }


def wrapper(data: dict) -> dict:
    return {"complete": True, "status": "complete", "data": data}


def write_live_files(root: Path, race_no: int = 1) -> None:
    race_dir = root / f"{race_no:02d}"
    race_dir.mkdir(parents=True)
    direct_racers = [
        {"lane": lane, "player_id": 4000 + lane}
        for lane in range(1, 7)
    ]
    exhibition = [
        {
            "lane": lane,
            "exhibition_course": lane,
            "exhibition_time": 6.65 + lane * 0.02,
            "start_time": 0.03 + lane * 0.01,
        }
        for lane in range(1, 7)
    ]
    original = [
        {
            "lane": lane,
            "lap_time": 37.0 + lane * 0.08,
            "turn_time": 5.3 + lane * 0.05,
            "straight_time": 7.0 + lane * 0.03,
        }
        for lane in range(1, 7)
    ]
    documents = {
        "direct": wrapper(
            {
                "actual_entry": [1, 2, 3, 4, 5, 6],
                "wind_direction": "南東",
                "wind_speed": 2.0,
                "wave_height": 2.0,
                "racers": direct_racers,
            }
        ),
        "exhibition": wrapper({"entries": exhibition}),
        "original_exhibition": wrapper({"entries": original}),
    }
    for name, document in documents.items():
        (race_dir / f"{name}.json").write_text(
            json.dumps(document, ensure_ascii=False),
            encoding="utf-8",
        )


class BiwakoV12IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "biwako.sqlite"
        create_database(self.db_path)
        self.engine = biwako.BiwakoPredictionEngineV12(
            self.db_path,
            biwako.CONFIG_PATH,
        )
        self.resolver = biwako.PlayerIdResolver(self.db_path)

    def tearDown(self) -> None:
        self.engine.close()
        self.resolver.close()
        self.temporary.cleanup()

    def apply(self, value: dict, stage: str, race: int | None = None) -> dict:
        return biwako.apply_predictions(
            value,
            DATE,
            self.engine,
            self.resolver,
            stage,
            self.root / "live",
            race,
        )

    def test_preliminary_generates_twelve_complete_predictions(self) -> None:
        result = self.apply(payload(), "preliminary")
        self.assertEqual(result["predictionStatus"], "ready")
        self.assertTrue(result["predictionAvailable"])
        self.assertEqual(result["engine_version"], "biwako_engine_v1.2_refined")
        self.assertEqual(len(result["preds"]), 12)
        for prediction in result["preds"].values():
            self.assertEqual(prediction["phase"], "preliminary")
            self.assertEqual(prediction["engine_version"], "biwako_engine_v1.2_refined")
            self.assertEqual(
                prediction["parameter_version"],
                "biwako_v1.2_refined_20260831",
            )
            self.assertEqual(len(prediction["tickets"]), 10)
            self.assertEqual(len({row["combo"] for row in prediction["tickets"]}), 10)
            for key in ("win", "second", "third"):
                self.assertAlmostEqual(sum(prediction[key].values()), 100.0, delta=0.05)
            self.assertEqual(
                prediction["diagnostics"]["oddsUsedForPrediction"],
                False,
            )

    def test_final_updates_only_complete_live_race(self) -> None:
        preliminary = self.apply(payload(), "preliminary")
        write_live_files(self.root / "live", 1)
        result = self.apply(preliminary, "final")
        self.assertEqual(result["preds"]["1"]["phase"], "final")
        self.assertEqual(result["races"][0]["predictionFinal"]["phase"], "final")
        self.assertEqual(result["races"][0]["predictionPre"]["phase"], "preliminary")
        self.assertEqual(result["preds"]["1"]["probabilityReviewStatus"], "reviewed")
        self.assertIn("deltaWin", result["preds"]["1"]["probabilityReview"]["1"])
        self.assertEqual(result["preds"]["2"]["phase"], "preliminary")

    def test_final_generates_all_twelve_and_preserves_preliminary(self) -> None:
        preliminary = self.apply(payload(), "preliminary")
        for race_no in range(1, 13):
            write_live_files(self.root / "live", race_no)

        result = self.apply(preliminary, "final")

        self.assertTrue(result["predictionAvailable"])
        self.assertEqual(result["predictionStatus"], "ready")
        self.assertEqual(result["engine_version"], "biwako_engine_v1.2_refined")
        self.assertEqual(result["predictionEngine"]["finalRaceCount"], 12)
        for race in result["races"]:
            race_no = str(race["race"])
            final = result["preds"][race_no]
            self.assertEqual(final["phase"], "final")
            self.assertEqual(final["engine_version"], "biwako_engine_v1.2_refined")
            self.assertEqual(race["predictionPre"]["phase"], "preliminary")
            self.assertEqual(race["predictionFinal"], final)
            self.assertEqual(len(final["tickets"]), 10)
            self.assertEqual(len({row["combo"] for row in final["tickets"]}), 10)

    def test_odds_and_result_do_not_change_preliminary_or_final(self) -> None:
        baseline_input = payload()
        mutated_input = copy.deepcopy(baseline_input)
        mutated_input["odds"] = {"1-2-3": 999.9}
        mutated_input["result"] = [6, 5, 4]
        for race in mutated_input["races"]:
            race["odds"] = {"6-5-4": 1.1}
            race["result"] = [6, 5, 4]

        baseline_pre = self.apply(baseline_input, "preliminary")
        mutated_pre = self.apply(mutated_input, "preliminary")
        keys = ("win", "second", "third", "sab", "tickets")
        for race_no in map(str, range(1, 13)):
            self.assertEqual(
                {key: baseline_pre["preds"][race_no][key] for key in keys},
                {key: mutated_pre["preds"][race_no][key] for key in keys},
            )

        for race_no in range(1, 13):
            write_live_files(self.root / "live", race_no)
        baseline_final = self.apply(baseline_pre, "final")
        mutated_final = self.apply(mutated_pre, "final")
        for race_no in map(str, range(1, 13)):
            self.assertEqual(
                {key: baseline_final["preds"][race_no][key] for key in keys},
                {key: mutated_final["preds"][race_no][key] for key in keys},
            )

    def test_incomplete_live_data_never_downgrades_preliminary(self) -> None:
        preliminary = self.apply(payload(), "preliminary")
        before = copy.deepcopy(preliminary)
        race_dir = self.root / "live" / "01"
        race_dir.mkdir(parents=True)
        (race_dir / "direct.json").write_text(
            json.dumps(wrapper({"racers": []})),
            encoding="utf-8",
        )
        result = self.apply(preliminary, "final", 1)
        self.assertEqual(result["preds"]["1"], before["preds"]["1"])
        self.assertEqual(result["predictionStatus"], "ready")

    def test_entry_change_keeps_probability_and_ticket_contracts(self) -> None:
        value = payload()
        value["races"][0]["racers"][0]["actual_course"] = 2
        value["races"][0]["racers"][1]["actual_course"] = 1

        result = self.apply(value, "preliminary")
        prediction = result["preds"]["1"]

        self.assertEqual(len(prediction["tickets"]), 10)
        self.assertEqual(len({row["combo"] for row in prediction["tickets"]}), 10)
        for key in ("win", "second", "third"):
            self.assertEqual(len(prediction[key]), 6)
            self.assertAlmostEqual(sum(prediction[key].values()), 100.0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
