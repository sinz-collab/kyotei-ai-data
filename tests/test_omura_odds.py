from __future__ import annotations

import ast
import asyncio
import itertools
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from apply_omura_odds import apply_odds, main


def without_odds(value):
    if isinstance(value, dict):
        return {key: without_odds(item) for key, item in value.items() if key != "odds"}
    if isinstance(value, list):
        return [without_odds(item) for item in value]
    return value


class TestOmuraOdds(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.odds = {"-".join(map(str, combo)): float(i + 1) for i, combo in enumerate(itertools.permutations(range(1, 7), 3))}
        self.document = {"date": "2026-09-18", "venue": "omura", "race_no": 1,
                         "status": "complete", "complete": True, "data": {"odds": self.odds}}
        prediction = {"win": {"1": 37.5}, "rank": [1, 4, 3, 6, 2, 5], "phase": "final",
                      "oddsUsedForPrediction": False, "odds": {},
                      "tickets": [{"combo": "1-4-6", "probability": 3.677, "odds": "-"}],
                      "ai": [{"combo": "1-4-6", "probability": 3.677, "odds": "-"}],
                      "aiUpset": [{"combo": "4-1-3", "probability": 1.491, "odds": "-"}]}
        self.payload = {"date": "2026-09-18", "races": [
            {"race": 1, "odds": {}, "predictionPre": {"odds": {}},
             "predictionFinal": deepcopy(prediction), "prediction": deepcopy(prediction)},
            {"race": 2, "odds": {}, "prediction": {"win": {"1": 50}}}],
            "preds": {"1": deepcopy(prediction)}}
        self.write_document()

    def write_document(self):
        (self.root / "odds.json").write_text(json.dumps(self.document), encoding="utf-8")

    def test_merges_only_odds_without_exhibition_or_engine(self):
        original = deepcopy(self.payload)
        self.assertTrue(apply_odds(self.payload, "2026-09-18", 1, self.root))
        self.assertEqual(without_odds(self.payload), without_odds(original))
        race = self.payload["races"][0]
        self.assertEqual(race["odds"], self.odds)
        for prediction in [race["predictionFinal"], race["prediction"], self.payload["preds"]["1"]]:
            for key in ["tickets", "ai", "aiUpset"]:
                for ticket in prediction[key]:
                    self.assertEqual(ticket["odds"], self.odds[ticket["combo"]])
        self.assertEqual(race["predictionPre"], original["races"][0]["predictionPre"])
        self.assertEqual(self.payload["races"][1], original["races"][1])
        self.assertFalse(apply_odds(self.payload, "2026-09-18", 1, self.root))

    def test_incomplete_or_wrong_identity_preserves_existing_data(self):
        for change in [{"date": "2026-09-17"}, {"venue": "toda"}, {"race_no": 2},
                       {"status": "pending"}, {"complete": False}, {"data": {"odds": {}}}]:
            with self.subTest(change=change):
                document = deepcopy(self.document)
                document.update(change)
                (self.root / "odds.json").write_text(json.dumps(document), encoding="utf-8")
                original = deepcopy(self.payload)
                self.assertFalse(apply_odds(self.payload, "2026-09-18", 1, self.root))
                self.assertEqual(self.payload, original)

    def test_cli_updates_dated_and_latest_preserves_other_files(self):
        data = self.root / "data"
        venue = data / "venues" / "omura"
        venue.mkdir(parents=True)
        for name in ["20260918.json", "latest.json", "20260917.json"]:
            (venue / name).write_text(json.dumps(self.payload), encoding="utf-8")
        untouched = (venue / "20260917.json").read_bytes()
        argv = ["apply_omura_odds.py", "--date", "2026-09-18", "--race", "1",
                "--data-root", str(data), "--live-root", str(self.root)]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(main(), 0)
        self.assertEqual((venue / "20260917.json").read_bytes(), untouched)
        for name in ["20260918.json", "latest.json"]:
            result = json.loads((venue / name).read_text(encoding="utf-8"))
            self.assertEqual(result["races"][0]["odds"], self.odds)

    def test_live_flow_runs_merger_only_for_omura(self):
        tree = ast.parse((ROOT / "scripts" / "live_fetch_once.py").read_text(encoding="utf-8"))
        node = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "apply_omura_live_odds")
        namespace = {"asyncio": asyncio, "sys": sys, "Path": Path, "Any": object,
                     "PUBLISHER_REPO": self.root, "OMURA_DATA_ROOT": self.root / "data"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "live_odds", "exec"), namespace)
        process = mock.Mock(returncode=0)
        process.communicate = mock.AsyncMock(return_value=(b"updated", b""))
        create = mock.AsyncMock(return_value=process)
        target = {"venue": "omura", "date": "2026-09-18", "race_no": 1}
        with mock.patch.object(asyncio, "create_subprocess_exec", create):
            asyncio.run(namespace["apply_omura_live_odds"](target, self.root, mock.Mock()))
            create.assert_awaited_once()
            self.assertIn(str(self.root / "automation" / "apply_omura_odds.py"), create.await_args.args)
            create.reset_mock()
            asyncio.run(namespace["apply_omura_live_odds"]({**target, "venue": "toda"}, self.root, mock.Mock()))
            create.assert_not_awaited()
        run_once = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_once")
        self.assertTrue(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "apply_omura_live_odds" for node in ast.walk(run_once)))


if __name__ == "__main__":
    unittest.main()
