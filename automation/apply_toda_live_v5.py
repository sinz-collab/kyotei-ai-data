from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "toda_v5"


def load_document(path: Path, required: bool = True) -> dict | None:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required: {path}")
    return payload


def complete_document(path: Path) -> dict | None:
    document = load_document(path, required=False)
    if not document:
        return None
    if document.get("complete") is not True or document.get("status") != "complete":
        return None
    return document


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def sync_race_prediction(payload: dict, race_no: int, prediction: dict, engine_id: str) -> None:
    race = next(
        (
            item
            for item in payload.get("races") or []
            if int(item.get("race") or 0) == race_no
        ),
        None,
    )
    if race is None:
        raise RuntimeError(f"toda_race_missing: {race_no}")

    envelope = race.setdefault("prediction", {})
    envelope.update(
        {
            "status": "ready",
            "reason": None,
            "engine": engine_id,
            "engine_version": engine_id,
            "probabilities": {
                key: deepcopy(prediction[key])
                for key in ("win", "second", "third")
            },
            "sab": prediction.get("sab"),
            "tickets": {
                key: deepcopy(prediction.get(key))
                for key in ("ai", "aiUpset", "balance", "tickets")
                if isinstance(prediction.get(key), list)
            },
            "probabilityReviewStatus": prediction.get("probabilityReviewStatus"),
            "probabilityFlow": deepcopy(prediction.get("probabilityFlow") or {}),
            "predictionStage": deepcopy(prediction.get("predictionStage") or {}),
        }
    )


def apply_toda_live_review(
    payload: dict,
    target_date: str,
    race_no: int,
    live_root: Path,
) -> dict:
    if payload.get("venueId") != "toda" or payload.get("date") != target_date:
        raise RuntimeError("toda_payload_identity_invalid")

    predictions = payload.get("preds") or {}
    prediction = predictions.get(str(race_no))
    if not isinstance(prediction, dict):
        raise RuntimeError(f"toda_prediction_missing: {race_no}")

    direct = complete_document(live_root / "direct.json")
    exhibition = complete_document(live_root / "exhibition.json")
    if direct is None or exhibition is None:
        raise RuntimeError("complete_direct_and_exhibition_required")

    # Original exhibition is useful when available, but never gates final review.
    original = complete_document(live_root / "original_exhibition.json")
    documents = {
        "direct": direct,
        "exhibition": exhibition,
    }
    if original is not None:
        documents["original_exhibition"] = original

    # Odds are intentionally not loaded or passed to the probability review.
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    from toda_live_review_v5 import apply_live_review
    from toda_prediction_engine_v5 import ENGINE_ID

    if not apply_live_review(prediction, documents):
        raise RuntimeError("toda_live_review_not_applied")

    prediction["engine"] = ENGINE_ID
    payload["engine"] = ENGINE_ID
    prediction_engine = payload.setdefault("predictionEngine", {})
    prediction_engine["id"] = ENGINE_ID
    prediction_engine["oddsUsedForProbability"] = False
    sync_race_prediction(payload, race_no, prediction, ENGINE_ID)
    return payload


def apply_file(target_date: str, race_no: int, data_root: Path, live_root: Path) -> dict:
    compact_date = target_date.replace("-", "")
    dated_path = data_root / "venues" / "toda" / f"{compact_date}.json"
    latest_path = data_root / "venues" / "toda" / "latest.json"
    payload = load_document(dated_path)
    assert payload is not None
    payload = apply_toda_live_review(payload, target_date, race_no, live_root)

    atomic_write_json(dated_path, payload)
    latest = load_document(latest_path, required=False)
    if latest is None or latest.get("date") == target_date:
        atomic_write_json(latest_path, payload)

    prediction = payload["preds"][str(race_no)]
    return {
        "date": target_date,
        "race": race_no,
        "engine": payload["engine"],
        "reviewed": prediction["probabilityFlow"]["reviewed"],
        "realtimeApplied": prediction["probabilityFlow"]["realtimeApplied"],
        "originalExhibitionApplied": prediction["liveReviewMeta"]["originalExhibitionApplied"],
        "oddsUsedForProbability": prediction["liveReviewMeta"]["oddsUsedForProbability"],
        "datedPath": str(dated_path),
        "latestUpdated": latest is None or latest.get("date") == target_date,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--live-root", type=Path, required=True)
    args = parser.parse_args()
    if args.race not in range(1, 13):
        raise ValueError("race_must_be_1_to_12")
    result = apply_file(args.date, args.race, args.data_root, args.live_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
