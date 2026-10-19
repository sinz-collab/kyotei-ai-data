from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from run_ashiya_v16 import (
    AshiyaEngine,
    MODELS_DIR,
    PLAYER_DB_DIR,
    ENGINE_NAME,
    ENGINE_VERSION,
    merge_race_payload,
    build_site_prediction,
    load_json,
    validate_source_payload,
    validate_final_payload,
    atomic_write_json,
    as_race_no,
    append_prediction_history,
)
from ashiya_live_v42 import apply_ashiya_live_v42


def lane_value(values, lane):
    if not isinstance(values, dict):
        return None

    value = values.get(str(lane), values.get(lane))

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def morning_value(old_prediction, lane, key, morning_key):
    review = old_prediction.get("probabilityReview") or {}

    lane_review = (
        review.get(str(lane))
        or review.get(lane)
        or {}
    )

    saved = lane_review.get(morning_key)

    try:
        if saved is not None:
            return float(saved)
    except (TypeError, ValueError):
        pass

    return lane_value(
        old_prediction.get(key) or {},
        lane,
    )


def build_probability_review(old_prediction, new_prediction):
    review = {}

    for lane in range(1, 7):
        morning_win = morning_value(
            old_prediction,
            lane,
            "win",
            "morningWin",
        )

        morning_second = morning_value(
            old_prediction,
            lane,
            "second",
            "morningSecond",
        )

        morning_third = morning_value(
            old_prediction,
            lane,
            "third",
            "morningThird",
        )

        new_win = lane_value(
            new_prediction.get("win") or {},
            lane,
        )

        new_second = lane_value(
            new_prediction.get("second") or {},
            lane,
        )

        new_third = lane_value(
            new_prediction.get("third") or {},
            lane,
        )

        review[str(lane)] = {
            "morningWin": morning_win,
            "win": new_win,
            "deltaWin": round(
                (new_win or 0.0) - (morning_win or 0.0),
                4,
            ),
            "morningSecond": morning_second,
            "second": new_second,
            "deltaSecond": round(
                (new_second or 0.0) - (morning_second or 0.0),
                4,
            ),
            "morningThird": morning_third,
            "third": new_third,
            "deltaThird": round(
                (new_third or 0.0) - (morning_third or 0.0),
                4,
            ),
        }

    return review


def morning_lane1_fly_probability(old_prediction):
    """Return the saved morning baseline, with a legacy JSON fallback."""
    saved = old_prediction.get("lane1FlyProbabilityMorning")
    try:
        value = float(saved)
        if 0.0 <= value <= 100.0:
            return round(value, 2)
    except (TypeError, ValueError):
        pass

    lane1_win = morning_value(
        old_prediction,
        1,
        "win",
        "morningWin",
    )
    if lane1_win is None or not 0.0 <= lane1_win <= 100.0:
        return None
    return round(100.0 - lane1_win, 2)


def attach_lane1_fly_comparison(old_prediction, legacy, native):
    """Keep morning and E-method LIVE probabilities as independent values."""
    morning = morning_lane1_fly_probability(old_prediction)
    live = legacy.get("lane1FlyProbabilityLive")
    if live is None and legacy.get("lane1FlyProbabilitySource") == "live":
        live = legacy.get("lane1FlyProbability")

    try:
        live = float(live)
        if not 0.0 <= live <= 100.0:
            live = None
    except (TypeError, ValueError):
        live = None

    fields = {
        "lane1FlyProbabilityMorning": morning,
        "lane1FlyProbabilityLive": round(live, 2) if live is not None else None,
        "lane1FlyProbabilityDeltaPt": (
            round(live - morning, 2)
            if live is not None and morning is not None
            else None
        ),
        "lane1FlyProbabilityDisplayStage": "live",
        "lane1FlyProbabilityIsProvisional": False,
    }
    legacy.update(deepcopy(fields))
    native.update(deepcopy(fields))


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        required=True,
        help="YYYY-MM-DD",
    )

    parser.add_argument(
        "--race",
        required=True,
        type=int,
    )

    parser.add_argument(
        "--data-root",
        default="data",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    args = parser.parse_args()

    if args.race < 1 or args.race > 12:
        raise RuntimeError(
            f"invalid_race_no: {args.race}"
        )

    data_root = Path(args.data_root)

    date_key = args.date.replace("-", "")

    dated_path = (
        data_root
        / "venues"
        / "ashiya"
        / f"{date_key}.json"
    )

    latest_path = (
        data_root
        / "venues"
        / "ashiya"
        / "latest.json"
    )

    payload = load_json(dated_path)

    validate_source_payload(
        payload,
        args.date,
    )

    race = next(
        (
            item
            for item in payload.get("races") or []
            if as_race_no(
                item.get("race")
                or item.get("race_no")
            )
            == args.race
        ),
        None,
    )

    if race is None:
        raise RuntimeError(
            f"ashiya_race_missing: {args.race}"
        )
    live = race.get("live")

    if not isinstance(live, dict):
        raise RuntimeError(
            f"ashiya_live_missing: {args.race}"
        )

    required_live = (
        "direct",
        "exhibition",
        "original",
    )

    missing_live = [
        key
        for key in required_live
        if not isinstance(
            live.get(key),
            dict,
        )
    ]

    if missing_live:
        raise RuntimeError(
            "ashiya_live_incomplete:"
            f"{args.race}:"
            + ",".join(missing_live)
        )

    predictions = payload.get("preds")

    if not isinstance(predictions, dict):
        raise RuntimeError(
            "ashiya_predictions_missing"
        )

    old_prediction = deepcopy(
        predictions.get(str(args.race))
        or {}
    )

    if not old_prediction:
        raise RuntimeError(
            f"ashiya_pre_prediction_missing: {args.race}"
        )

    merged = merge_race_payload(
        payload,
        race,
    )

    engine = AshiyaEngine(
        MODELS_DIR,
        PLAYER_DB_DIR,
    )

    result = engine.predict(
        merged,
        stage="live",
    )

    if not isinstance(result, dict):
        raise RuntimeError(
            "ashiya_live_engine_output_invalid"
        )

    result = apply_ashiya_live_v42(
        result,
        merged,
    )

    legacy, native = build_site_prediction(
        result,
        args.race,
        "live",
    )

    append_prediction_history(
        legacy,
        native,
        old_prediction,
        result,
        merged,
        "live",
    )

    attach_lane1_fly_comparison(
        old_prediction,
        legacy,
        native,
    )

    review = build_probability_review(
        old_prediction,
        legacy,
    )

    stage = {
        "label": "本予想",
        "badge": "本予想",
        "statusText": (
            "展示・スリット・直前情報を反映して"
            "芦屋v5.1で再精査済み"
        ),
    }

    flow = {
        "baseLabel": "直前前エンジン予想",
        "realtimeApplied": True,
        "realtimeLabel": "展示・スリット・直前反映",
        "reviewed": True,
        "reviewLabel": "再精査後の調整数字",
    }

    legacy["predictionStage"] = stage
    legacy["active_prediction_stage"] = "live"

    legacy["probabilityReviewStatus"] = (
        "reviewed"
    )

    legacy["probabilityReview"] = review
    legacy["probabilityFlow"] = flow

    native["predictionStage"] = deepcopy(stage)

    native["probabilityReviewStatus"] = (
        "reviewed"
    )

    native["probabilityReview"] = deepcopy(
        review
    )

    native["probabilityFlow"] = deepcopy(
        flow
    )

    predictions[str(args.race)] = legacy

    race["prediction"] = native

    payload["preds"] = predictions

    payload["engine"] = ENGINE_NAME
    payload["engineVersion"] = ENGINE_VERSION
    payload["predictionStatus"] = "ready"
    payload["predictionReason"] = ""

    validate_final_payload(
        payload,
        args.date,
    )

    changed = {
        str(lane): {
            "win": review[str(lane)]["deltaWin"],
            "second": review[str(lane)]["deltaSecond"],
            "third": review[str(lane)]["deltaThird"],
        }
        for lane in range(1, 7)
    }

    report = {
        "date": args.date,
        "venue": "ashiya",
        "race": args.race,
        "engine": ENGINE_NAME,
        "version": ENGINE_VERSION,
        "stage": "live",
        "predictionStage": "本予想",
        "tickets": len(
            legacy.get("tickets") or []
        ),
        "probabilityReviewStatus": (
            legacy.get(
                "probabilityReviewStatus"
            )
        ),
        "deltas": changed,
        "dryRun": bool(args.dry_run),
    }

    if not args.dry_run:
        atomic_write_json(
            dated_path,
            payload,
        )

        atomic_write_json(
            latest_path,
            payload,
        )

    print(
        json.dumps(
            report,
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
