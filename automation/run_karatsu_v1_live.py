from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from apply_karatsu_v1 import ENGINE_ID, atomic_write_json, site_prediction


REQUIRED_LIVE_DOCUMENTS = ("direct", "exhibition", "original_exhibition")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def complete_live_documents(race_dir: Path) -> dict[str, dict] | None:
    documents: dict[str, dict] = {}
    for name in REQUIRED_LIVE_DOCUMENTS:
        path = race_dir / f"{name}.json"
        if not path.is_file():
            return None
        document = load_json(path)
        if document.get("complete") is not True or document.get("status") != "complete":
            return None
        if not isinstance(document.get("data"), dict):
            return None
        documents[name] = document

    if len(documents["direct"]["data"].get("racers") or []) != 6:
        return None
    if len(documents["exhibition"]["data"].get("entries") or []) != 6:
        return None
    if len(documents["original_exhibition"]["data"].get("entries") or []) != 6:
        return None
    return documents


def prediction_snapshot(prediction: dict, stage: str, generated_at: str | None) -> dict:
    return {
        "snapshotVersion": "karatsu_prediction_snapshot_v1",
        "stage": stage,
        "generatedAt": generated_at,
        "engine": prediction.get("engine"),
        "engineVersion": prediction.get("engineVersion"),
        "sab": prediction.get("sab"),
        "probabilities": {
            key: deepcopy(prediction.get(key) or {})
            for key in ("win", "second", "third")
        },
        "ai": deepcopy(prediction.get("ai") or []),
        "aiUpset": deepcopy(prediction.get("aiUpset") or []),
    }


def append_history(final: dict, morning: dict, previous: Any, generated_at: str) -> None:
    history = deepcopy(
        previous.get("prediction_history")
        if isinstance(previous, dict)
        else {}
    )
    if not isinstance(history, dict):
        history = {}
    if not isinstance(history.get("morning"), list) or not history["morning"]:
        history["morning"] = [prediction_snapshot(morning, "morning", None)]
    live = history.get("live")
    if not isinstance(live, list):
        live = []
    snapshot = prediction_snapshot(final, "final", generated_at)
    if not live or live[-1] != snapshot:
        live.append(snapshot)
    history["live"] = live
    final["prediction_history"] = history


def probability_review(morning: dict, final: dict) -> dict:
    review = {}
    for lane in range(1, 7):
        key = str(lane)
        review[key] = {}
        for position, label in (("win", "Win"), ("second", "Second"), ("third", "Third")):
            before = float((morning.get(position) or {}).get(key, 0))
            after = float((final.get(position) or {}).get(key, 0))
            review[key][f"morning{label}"] = before
            review[key][position] = after
            review[key][f"delta{label}"] = round(after - before, 1)
    return review


def apply_live_race(payload: dict, race_no: int, documents: dict[str, dict]) -> dict:
    races = payload.get("races") or []
    race = next((item for item in races if int(item.get("race") or 0) == race_no), None)
    if race is None:
        raise RuntimeError(f"karatsu_race_missing:{race_no}")

    previous = race.get("prediction")
    morning = deepcopy(race.get("predictionPre") or previous or (payload.get("preds") or {}).get(str(race_no)))
    if not isinstance(morning, dict) or morning.get("engine") != ENGINE_ID:
        raise RuntimeError(f"karatsu_morning_prediction_missing:{race_no}")

    direct = documents["direct"]["data"]
    exhibition = documents["exhibition"]["data"]
    original = documents["original_exhibition"]["data"]
    generated_at = max(
        str(document.get("fetched_at") or "")
        for document in documents.values()
    )
    if generated_at:
        datetime.fromisoformat(generated_at)

    race["live"] = {
        **deepcopy(race.get("live") or {}),
        "actual_entry": deepcopy(direct.get("actual_entry") or []),
        "entry_changed": bool(direct.get("entry_changed")),
        "weather": {
            "weather": direct.get("weather"),
            "air_temperature": direct.get("air_temperature"),
            "water_temperature": direct.get("water_temperature"),
            "wind_direction": direct.get("wind_direction"),
            "wind_speed": direct.get("wind_speed"),
            "wave_height": direct.get("wave_height"),
        },
        "exhibition": deepcopy(exhibition),
        "original": deepcopy(original),
    }

    final = site_prediction(payload, race)
    final.update({
        "phase": "final",
        "finalPredictionStatus": "complete",
        "generatedAt": generated_at,
        "predictionStage": {
            "code": "final",
            "label": "本予想",
            "badge": "本予想",
            "statusText": "直前・展示・オリ展示を反映して唐津v1.2で再精査済み",
        },
        "probabilityReviewStatus": "reviewed",
        "probabilityFlow": {
            "required": True,
            "baseApplied": True,
            "realtimeApplied": True,
            "reviewed": True,
            "adjustedRequired": True,
        },
        "liveInput": {
            "required": list(REQUIRED_LIVE_DOCUMENTS),
            "complete": True,
            "fetchedAt": generated_at,
        },
    })
    final["probabilityReview"] = probability_review(morning, final)
    append_history(final, morning, previous, generated_at)

    race["predictionPre"] = morning
    race["predictionFinal"] = deepcopy(final)
    race["prediction"] = deepcopy(final)
    predictions = payload.setdefault("preds", {})
    predictions[str(race_no)] = deepcopy(final)
    payload["predictionStatus"] = "ready"
    payload["predictionReason"] = ""
    return final


def run(target_date: str, race_no: int, data_root: Path, race_dir: Path) -> dict:
    compact = target_date.replace("-", "")
    dated_path = data_root / "venues" / "karatsu" / f"{compact}.json"
    latest_path = dated_path.with_name("latest.json")
    payload = load_json(dated_path)
    if payload.get("venueId") != "karatsu" or payload.get("date") != target_date:
        raise RuntimeError("karatsu_payload_identity_invalid")

    documents = complete_live_documents(race_dir)
    if documents is None:
        return {"status": "skipped", "reason": "required_live_data_incomplete", "race": race_no}

    final = apply_live_race(payload, race_no, documents)
    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)
    if dated_path.read_bytes() != latest_path.read_bytes():
        raise RuntimeError("karatsu_dated_latest_mismatch")
    return {
        "status": "complete",
        "race": race_no,
        "generatedAt": final["generatedAt"],
        "sab": final["sab"],
        "tickets": len(final["ai"]) + len(final["aiUpset"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--race", type=int, required=True, choices=range(1, 13))
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--live-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.date, args.race, args.data_root, args.live_root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
