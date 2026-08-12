from __future__ import annotations

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from publish_live_data import (
    publish_tokoname_predictions,
    without_prediction_fields,
)


DATE = "2026-07-31"
COMPACT_DATE = "20260731"


def prediction() -> dict:
    probabilities = {
        key: {str(lane): value for lane, value in enumerate((30, 20, 15, 15, 10, 10), 1)}
        for key in ("win", "second", "third")
    }
    tickets = {
        "main": [{"combination": value} for value in ("1-2-3", "1-2-4", "1-3-2", "1-3-4", "2-1-3", "2-1-4")],
        "deviation": [{"combination": value} for value in ("3-1-2", "3-1-4")],
        "upset": [{"combination": value} for value in ("4-1-2", "5-1-2")],
    }
    return {
        "status": "ready",
        "reason": None,
        "engine": "tokoname_engine",
        "engine_version": "1.6",
        "prediction_phase": "final",
        "stage": "final",
        "engine_recalculated_after_exhibition": True,
        "original_exhibition_available": True,
        "engine_run": {
            "completed": True,
            "source_engine": "tokoname_engine_v1.6",
            "mode": "exhibition_recalculation",
            "inputs": [
                "morning",
                "direct",
                "exhibition",
                "original_exhibition",
                "odds",
            ],
        },
        "predictionStage": {"code": "final", "label": "本予想"},
        "probabilities": probabilities,
        "sab": {"rank": "A"},
        "tickets": tickets,
        "scenario": {},
        "data_flags": {
            "original_exhibition_available": True,
            "odds_used_for_probability": False,
            "result_used_for_probability": False,
        },
    }


def venue_document() -> dict:
    races = []
    for race_no in range(1, 13):
        races.append(
            {
                "race": race_no,
                "racers": [{"lane": lane} for lane in range(1, 7)],
                "setsukan": [{"race": race_no}],
                "live": {"direct": race_no},
                "odds": {"3t": race_no},
                "result": {"order": [1, 2, 3]},
                "prediction": {
                    "status": "unavailable",
                    "reason": "prediction_payload_unavailable",
                },
            }
        )
    return {
        "date": DATE,
        "venueId": "tokoname",
        "venue": "常滑",
        "engine": "",
        "predictionStatus": "unavailable",
        "predictionReason": "prediction_payload_unavailable",
        "races": races,
    }


def manifest() -> dict:
    return {
        "date": DATE,
        "dateDir": COMPACT_DATE,
        "venues": [
            {
                "slug": "karatsu",
                "open": True,
                "predictionAvailable": True,
                "predictionStatus": "ready",
            },
            {
                "slug": "tokoname",
                "open": True,
                "predictionAvailable": False,
                "prediction_available": False,
                "predictionStatus": "unavailable",
                "prediction_status": "unavailable",
                "predictionReason": "prediction_payload_unavailable",
                "prediction_reason": "prediction_payload_unavailable",
                "availabilityReason": "prediction_payload_unavailable",
            },
        ],
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class TokonamePredictionPublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging_root = self.root / "runtime" / "predictions"
        self.repo_root = self.root / "repository"
        self.dated = (
            self.repo_root / "data" / "venues" / "tokoname" / f"{COMPACT_DATE}.json"
        )
        self.latest = self.repo_root / "data" / "venues" / "tokoname" / "latest.json"
        self.manifest_path = self.repo_root / "data" / "manifest.json"
        self.original = venue_document()
        write_json(self.dated, self.original)
        write_json(self.latest, self.original)
        write_json(self.manifest_path, manifest())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stage(self, payload: dict) -> None:
        write_json(
            self.staging_root
            / "venues"
            / "tokoname"
            / f"{COMPACT_DATE}.json",
            payload,
        )

    def test_ready_race_publishes_incrementally_without_other_domain_changes(self) -> None:
        staged = deepcopy(self.original)
        staged["races"][0]["prediction"] = {
            **prediction(),
            "input_hash": "race-1",
        }
        self.stage(staged)
        manifest_before = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        result = publish_tokoname_predictions(self.staging_root, self.repo_root)

        self.assertEqual(result["status"], "published")
        self.assertEqual(result["published_races"], [1])
        self.assertEqual(result["available_races"], [1])
        published = json.loads(self.dated.read_text(encoding="utf-8"))
        self.assertEqual(self.dated.read_bytes(), self.latest.read_bytes())
        self.assertEqual(
            without_prediction_fields(published),
            without_prediction_fields(self.original),
        )
        self.assertEqual(published["races"][0]["prediction"]["engine_version"], "1.6")
        self.assertEqual(published["races"][0]["prediction"]["input_hash"], "race-1")
        self.assertEqual(
            published["races"][1]["prediction"]["status"],
            "unavailable",
        )
        published_manifest = json.loads(
            self.manifest_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            published_manifest["venues"][0],
            manifest_before["venues"][0],
        )
        tokoname = published_manifest["venues"][1]
        self.assertTrue(tokoname["predictionAvailable"])
        self.assertEqual(tokoname["predictionStatus"], "ready")

    def test_failed_or_empty_prediction_is_not_published(self) -> None:
        staged = deepcopy(self.original)
        staged["races"][4]["prediction"] = {
            "status": "unavailable",
            "reason": "engine_failed",
        }
        self.stage(staged)
        before = snapshot(self.repo_root)

        result = publish_tokoname_predictions(self.staging_root, self.repo_root)

        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(snapshot(self.repo_root), before)

    def test_ready_status_without_final_engine_proof_is_not_published(self) -> None:
        staged = deepcopy(self.original)
        legacy = prediction()
        for key in (
            "prediction_phase",
            "stage",
            "engine_recalculated_after_exhibition",
            "engine_run",
        ):
            legacy.pop(key, None)
        staged["races"][0]["prediction"] = legacy
        self.stage(staged)
        before = snapshot(self.repo_root)

        result = publish_tokoname_predictions(self.staging_root, self.repo_root)

        self.assertEqual(result["status"], "not_ready")
        self.assertEqual(snapshot(self.repo_root), before)

    def test_same_content_is_unchanged(self) -> None:
        staged = deepcopy(self.original)
        staged["races"][0]["prediction"] = {
            **prediction(),
            "input_hash": "same",
        }
        self.stage(staged)
        first = publish_tokoname_predictions(self.staging_root, self.repo_root)
        before = snapshot(self.repo_root)

        second = publish_tokoname_predictions(self.staging_root, self.repo_root)

        self.assertEqual(first["status"], "published")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(snapshot(self.repo_root), before)

    def test_only_changed_ready_race_is_republished(self) -> None:
        staged = deepcopy(self.original)
        staged["races"][0]["prediction"] = {
            **prediction(),
            "input_hash": "first",
        }
        self.stage(staged)
        publish_tokoname_predictions(self.staging_root, self.repo_root)
        before = json.loads(self.dated.read_text(encoding="utf-8"))

        staged["races"][1]["prediction"] = {
            **prediction(),
            "input_hash": "second",
        }
        self.stage(staged)
        result = publish_tokoname_predictions(self.staging_root, self.repo_root)
        after = json.loads(self.dated.read_text(encoding="utf-8"))

        self.assertEqual(result["published_races"], [2])
        self.assertEqual(
            after["races"][0]["prediction"],
            before["races"][0]["prediction"],
        )
        self.assertEqual(after["races"][1]["prediction"]["input_hash"], "second")


if __name__ == "__main__":
    unittest.main()
