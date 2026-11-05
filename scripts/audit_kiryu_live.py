#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from run_kiryu_v1 import (  # noqa: E402
    LIVE_REQUIRED_FEATURES,
    build_live_calculation_input,
    complete_live_document,
)


POSITIONS = ("win", "second", "third")
DOCUMENTS = ("direct", "exhibition", "original_exhibition")
FAILURE_REASON = "kiryu_live_probability_calculation_not_executed"


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def probabilities(prediction: dict | None) -> dict | None:
    if not prediction:
        return None
    return {
        position: {
            str(lane): prediction.get(position, {}).get(str(lane))
            for lane in range(1, 7)
        }
        for position in POSITIONS
    }


def probability_deltas(morning: dict, final: dict) -> dict:
    return {
        position: {
            str(lane): round(
                float(final[position][str(lane)])
                - float(morning[position][str(lane)]),
                10,
            )
            for lane in range(1, 7)
        }
        for position in POSITIONS
    }


def load_documents(live_root: Path, race_no: int) -> dict[str, dict]:
    race_root = live_root / f"{race_no:02d}"
    return {
        name: json.loads((race_root / f"{name}.json").read_text(encoding="utf-8"))
        for name in DOCUMENTS
    }


def document_audit(documents: dict[str, dict]) -> dict:
    return {
        name: {
            "complete": complete_live_document(document, name),
            "contentHash": document.get("content_hash"),
            "fetchedAt": document.get("fetched_at"),
        }
        for name, document in documents.items()
    }


def fallback_prediction(morning: dict, documents: dict[str, dict], input_hash: str) -> dict:
    current = deepcopy(morning)
    current.update({
        "phase": "preliminary",
        "status": "preliminary",
        "finalPredictionStatus": "failed",
        "predictionFailureReason": FAILURE_REASON,
        "probabilityReviewStatus": "not_calculated",
        "probabilityFlow": {
            "required": True,
            "baseApplied": True,
            "baseLabel": "朝予想",
            "realtimeApplied": False,
            "realtimeLabel": "未計算",
            "reviewed": False,
            "reviewLabel": "本予想未成立",
            "adjustedRequired": True,
        },
        "liveInput": {
            "complete": True,
            "inputHash": input_hash,
            "required": list(DOCUMENTS),
            "documentHashes": {
                name: document.get("content_hash")
                for name, document in documents.items()
            },
            "fetchedAt": {
                name: document.get("fetched_at")
                for name, document in documents.items()
            },
        },
        "liveCalculation": {
            "executed": False,
            "status": "not_executed",
            "reason": FAILURE_REASON,
            "inputHash": input_hash,
            "featuresReceived": list(LIVE_REQUIRED_FEATURES),
            "probabilityFeaturesUsed": [],
        },
    })
    current.pop("probabilityReview", None)
    return current


def audit_payload(payload: dict, live_root: Path, repair: bool) -> dict:
    races = {int(race["race"]): race for race in payload.get("races") or []}
    report = {
        "venue": "kiryu",
        "date": payload.get("date"),
        "engine": payload.get("engine"),
        "finding": {
            "primaryCause": "uncomputed_live_features",
            "categories": {
                "missingInput": False,
                "notCalculated": True,
                "zeroCorrection": False,
                "saveBug": True,
                "displayBug": False,
            },
            "detail": (
                "The saved LIVE documents were complete, but the production engine only "
                "read exhibition course changes. Exhibition ST/time, original exhibition, "
                "and weather were not probability features. No prediction input hash was saved."
            ),
        },
        "races": [],
    }

    for race_no in range(1, 13):
        race = races[race_no]
        morning = race.get("predictionPre")
        saved_final = race.get("predictionFinal") or race.get("prediction")
        documents = load_documents(live_root, race_no)
        live_input, input_hash = build_live_calculation_input(documents)
        entry = "".join(
            str(row["lane"])
            for row in sorted(live_input["boats"], key=lambda row: row["exhibition_course"])
        )
        deltas = probability_deltas(morning, saved_final)
        flat_deltas = [
            abs(value)
            for position in POSITIONS
            for value in deltas[position].values()
        ]
        row = {
            "race": race_no,
            "documents": document_audit(documents),
            "combinedInputHash": input_hash,
            "storedPredictionInputHash": saved_final.get("input_hash"),
            "inputSnapshot": live_input,
            "morningProbabilities": probabilities(morning),
            "savedFinalProbabilities": probabilities(saved_final),
            "deltas": deltas,
            "nonzeroRawDeltaCount": sum(value > 1e-12 for value in flat_deltas),
            "maxAbsRawDelta": max(flat_deltas),
            "displayedAllZeroAtOneDecimal": all(value < 0.05 for value in flat_deltas),
            "entryOrder": entry,
            "entryChanged": entry != "123456",
            "storedStage": saved_final.get("predictionStage"),
            "storedFinalStatus": saved_final.get("finalPredictionStatus"),
            "featuresUsedByStoredEngine": ["entry_changed_boolean"],
            "featuresNotUsedByStoredEngine": [
                feature for feature in LIVE_REQUIRED_FEATURES
                if feature != "exhibition_course"
            ],
            "calculationState": "partial_entry_only",
            "repairAction": "morning_fallback_and_final_failure" if repair else None,
        }
        report["races"].append(row)

        if repair:
            current = fallback_prediction(morning, documents, input_hash)
            race.pop("predictionFinal", None)
            race["prediction"] = current
            payload.setdefault("preds", {})[str(race_no)] = deepcopy(current)

    report["summary"] = {
        "raceCount": len(report["races"]),
        "completeLiveInputCount": sum(
            all(value["complete"] for value in row["documents"].values())
            for row in report["races"]
        ),
        "successfulLiveCalculationCount": 0,
        "invalidatedFalseFinalCount": 12 if repair else 0,
    }
    if repair:
        payload["livePredictionIntegrity"] = {
            "status": "failed",
            "reason": FAILURE_REASON,
            "failedRaceCount": 12,
            "auditPath": "audits/kiryu/20261105-live-integrity.json",
        }
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()

    compact = args.date.replace("-", "")
    data_root = Path(args.data_root)
    dated = data_root / "venues" / "kiryu" / f"{compact}.json"
    latest = data_root / "venues" / "kiryu" / "latest.json"
    live_root = data_root / "live" / args.date / "kiryu"
    payload = json.loads(dated.read_text(encoding="utf-8"))
    report = audit_payload(payload, live_root, args.repair)
    report_path = data_root / "audits" / "kiryu" / f"{compact}-live-integrity.json"
    atomic(report_path, report)
    if args.repair:
        atomic(dated, payload)
        if latest.exists():
            latest_payload = json.loads(latest.read_text(encoding="utf-8"))
            if latest_payload.get("date") == payload.get("date"):
                atomic(latest, payload)
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
