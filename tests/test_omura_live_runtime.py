from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "automation", ROOT / "scripts"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import apply_omura_v1 as omura  # noqa: E402
import live_fetch_once  # noqa: E402

if str(omura.ENGINE_SRC) not in sys.path:
    sys.path.insert(0, str(omura.ENGINE_SRC))
import omura_engine  # noqa: E402,F401


def fetch_result(complete: bool = True) -> dict:
    return {
        "items": {
            name: {
                "complete": complete,
                "status": "complete" if complete else "pending",
            }
            for name in ("direct", "exhibition", "original_exhibition")
        }
    }


class FakeProcess:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return b'{"updated": true}', b"prediction failed"


class TestOmuraLiveFetchConnection(unittest.TestCase):
    def setUp(self) -> None:
        self.target = {"venue": "omura", "date": "2026-09-03", "race_no": 1}
        self.logger = mock.Mock()

    def test_all_three_complete_runs_final_prediction_once(self) -> None:
        create = mock.AsyncMock(return_value=FakeProcess())
        with mock.patch.object(asyncio, "create_subprocess_exec", create):
            asyncio.run(
                live_fetch_once.apply_omura_live_prediction(
                    self.target, Path("race"), fetch_result(), self.logger
                )
            )
        create.assert_awaited_once()
        arguments = create.await_args.args
        self.assertIn("--stage", arguments)
        self.assertEqual(arguments[arguments.index("--stage") + 1], "final")
        self.assertEqual(arguments[arguments.index("--race") + 1], "1")

    def test_any_incomplete_item_does_not_run_prediction(self) -> None:
        result = fetch_result()
        result["items"]["original_exhibition"]["complete"] = False
        create = mock.AsyncMock()
        with mock.patch.object(asyncio, "create_subprocess_exec", create):
            asyncio.run(
                live_fetch_once.apply_omura_live_prediction(
                    self.target, Path("race"), result, self.logger
                )
            )
        create.assert_not_awaited()

    def test_other_venue_is_unchanged(self) -> None:
        create = mock.AsyncMock()
        with mock.patch.object(asyncio, "create_subprocess_exec", create):
            asyncio.run(
                live_fetch_once.apply_omura_live_prediction(
                    {**self.target, "venue": "fukuoka"},
                    Path("race"),
                    fetch_result(),
                    self.logger,
                )
            )
        create.assert_not_awaited()


class TestOmuraLivePrediction(unittest.TestCase):
    @staticmethod
    def payload() -> dict:
        races = []
        predictions = {}
        for race_no in range(1, 13):
            prediction = {"marker": f"morning-{race_no}"}
            predictions[str(race_no)] = deepcopy(prediction)
            races.append(
                {
                    "race": race_no,
                    "deadline": "12:00",
                    "racers": [
                        {"lane": lane, "name": f"racer-{lane}"}
                        for lane in range(1, 7)
                    ],
                    "prediction": prediction,
                }
            )
        return {
            "venueId": "omura",
            "venue": "大村",
            "date": "2026-09-03",
            "races": races,
            "preds": predictions,
        }

    @staticmethod
    def write_live_documents(race_dir: Path) -> None:
        common = {
            "complete": True,
            "status": "complete",
            "date": "2026-09-03",
            "venue": "omura",
            "race_no": 1,
        }
        documents = {
            "direct": {
                **common,
                "data": {
                    "actual_entry": [1, 2, 3, 4, 5, 6],
                    "racers": [
                        {"lane": lane, "tilt": 0.0} for lane in range(1, 7)
                    ],
                },
            },
            "exhibition": {
                **common,
                "data": {
                    "entries": [
                        {
                            "lane": lane,
                            "exhibition_rank": lane,
                            "start_time": lane / 100,
                        }
                        for lane in range(1, 7)
                    ]
                },
            },
            "original_exhibition": {
                **common,
                "data": {
                    "entries": [
                        {"lane": lane, "sum": 44.0 + lane / 10}
                        for lane in range(1, 7)
                    ]
                },
            },
        }
        race_dir.mkdir(parents=True)
        for name, document in documents.items():
            (race_dir / f"{name}.json").write_text(
                json.dumps(document), encoding="utf-8"
            )

    def test_engine_failure_preserves_existing_preliminary_prediction(self) -> None:
        payload = self.payload()
        before = deepcopy(payload)
        with tempfile.TemporaryDirectory() as temporary:
            race_dir = Path(temporary) / "01"
            self.write_live_documents(race_dir)
            engine = mock.Mock()
            engine.predict.side_effect = RuntimeError("engine failure")
            with (
                mock.patch.object(omura, "load_player_index", return_value={}),
                mock.patch("omura_engine.OmuraPredictionEngine", return_value=engine),
                self.assertRaisesRegex(RuntimeError, "engine failure"),
            ):
                omura.apply_omura_live_race(
                    payload, "2026-09-03", 1, race_dir
                )
        self.assertEqual(payload, before)

    @unittest.skipUnless(
        (ROOT / "data" / "venues" / "omura" / "20260903.json").is_file(),
        "2026-09-03 Omura saved data is unavailable",
    )
    def test_replay_20260903_updates_only_target_race_to_final(self) -> None:
        dated = ROOT / "data" / "venues" / "omura" / "20260903.json"
        live = ROOT / "data" / "live" / "2026-09-03" / "omura" / "01"
        payload = json.loads(dated.read_text(encoding="utf-8"))
        before = deepcopy(payload)

        updated, changed = omura.apply_omura_live_race(
            payload, "2026-09-03", 1, live
        )

        self.assertTrue(changed)
        self.assertEqual(payload, before)
        final = updated["races"][0]["prediction"]
        self.assertEqual(final["predictionStage"]["label"], "本予想")
        self.assertEqual(final["phase"], "final")
        self.assertTrue(final["probabilityFlow"]["realtimeApplied"])
        self.assertFalse(final["oddsUsedForPrediction"])
        self.assertEqual(updated["races"][1], before["races"][1])
        self.assertEqual(updated["preds"]["2"], before["preds"]["2"])


if __name__ == "__main__":
    unittest.main()
