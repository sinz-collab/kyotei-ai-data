from __future__ import annotations

from decimal import Decimal, InvalidOperation
from itertools import permutations
from typing import Any


MODEL_VERSION = "wakamatsu_fly_tickets_v1"
METHOD = "live_place_product_top10"
PROBABILITY_METHOD = "normalized_place_product_all_120"
PROBABILITY_CALIBRATION = "not_empirically_calibrated"
LANES = (1, 2, 3, 4, 5, 6)
EVENT_TYPES = {"general", "rookie", "women", "unknown"}


def event_type_from_label(label: Any) -> str:
    """Fallback for already-published payloads whose label came from official headers."""
    text = str(label or "")
    if "♥" in text:
        return "women"
    if "🔰" in text:
        return "rookie"
    return "unknown"


def normalize_event_type(value: Any, event_label: Any = "") -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in EVENT_TYPES else event_type_from_label(event_label)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value in ("", "-", "―"):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


def fly_level(probability: Any, event_type: str) -> str | None:
    value = _decimal(probability)
    if value is None:
        return None
    yellow, orange, red = (
        (Decimal("55"), Decimal("60"), Decimal("65"))
        if event_type == "general"
        else (Decimal("50"), Decimal("55"), Decimal("60"))
    )
    if value >= red:
        return "危険"
    if value >= orange:
        return "警戒"
    if value >= yellow:
        return "注意"
    return "通常"


def _position_rates(prediction: dict, position: str) -> tuple[dict[int, Decimal], list[str]]:
    values = prediction.get(position)
    values = values if isinstance(values, dict) else {}
    rates: dict[int, Decimal] = {}
    missing: list[str] = []
    for lane in LANES:
        value = _decimal(values.get(str(lane), values.get(lane)))
        if value is None:
            missing.append(f"{position}.{lane}")
        else:
            rates[lane] = value
    return rates, missing


def build_fly_prediction(prediction: dict, source: str, event_type: str) -> dict:
    win, missing_win = _position_rates(prediction, "win")
    second, missing_second = _position_rates(prediction, "second")
    third, missing_third = _position_rates(prediction, "third")
    missing = missing_win + missing_second + missing_third

    lane1_win = win.get(1)
    fly_probability = None if lane1_win is None else Decimal("100") - lane1_win
    if fly_probability is not None and fly_probability < 0:
        fly_probability = None
        missing.append("win.1_out_of_range")

    prediction["lane1FlyProbability"] = (
        float(fly_probability) if fly_probability is not None else None
    )
    prediction["lane1FlyProbabilitySource"] = source if fly_probability is not None else None
    prediction["lane1FlyLevel"] = fly_level(fly_probability, event_type)

    fly: dict[str, Any] = {
        "modelVersion": MODEL_VERSION,
        "source": source,
        "method": METHOD,
        "probabilityMethod": PROBABILITY_METHOD,
        "probabilityCalibration": PROBABILITY_CALIBRATION,
        "status": "unavailable" if missing else "complete",
        "tickets": [],
    }
    if missing:
        fly["missingFields"] = sorted(set(missing))
        prediction["flyPrediction"] = fly
        return fly

    all_candidates = []
    for head in LANES:
        for second_lane, third_lane in permutations((lane for lane in LANES if lane != head), 2):
            score = win[head] * second[second_lane] * third[third_lane]
            all_candidates.append(((head, second_lane, third_lane), score))

    normalization_total = sum((score for _, score in all_candidates), Decimal("0"))
    probabilities = (
        {combo: score / normalization_total * Decimal("100") for combo, score in all_candidates}
        if normalization_total > 0
        else {}
    )
    candidates = [item for item in all_candidates if item[0][0] != 1]

    # Decimal scores preserve exact ties; the lane tuple is the deterministic tiebreaker.
    candidates.sort(key=lambda item: (-item[1], item[0]))
    fly["candidateCount"] = len(candidates)
    fly["normalizationCandidateCount"] = len(all_candidates)
    fly["normalizationScoreTotal"] = (
        float(normalization_total) if normalization_total > 0 else None
    )
    fly["probabilityStatus"] = "complete" if normalization_total > 0 else "unavailable"
    fly["tickets"] = [
        {
            "combo": "-".join(map(str, combo)),
            "score": float(score),
            "probability": float(probabilities[combo]) if combo in probabilities else None,
            "role": "main",
        }
        for combo, score in candidates[:10]
    ]
    prediction["flyPrediction"] = fly
    return fly


def apply_wakamatsu_fly_v1(payload: dict) -> dict:
    event_type = normalize_event_type(payload.get("eventType"), payload.get("eventLabel"))
    payload["eventType"] = event_type
    for race in payload.get("races") or []:
        race["eventType"] = event_type
        pre = race.get("predictionPre")
        final = race.get("predictionFinal")
        if isinstance(pre, dict):
            build_fly_prediction(pre, "morning_provisional", event_type)
            pre["lane1FlyProbabilityModel"] = "morning_lane1_win_complement_provisional"
            pre["lane1FlyProbabilityStatus"] = (
                "ok" if pre.get("lane1FlyProbability") is not None else "unavailable"
            )
            pre["lane1FlyProbabilityReason"] = (
                "validated_morning_complement"
                if pre.get("lane1FlyProbability") is not None
                else "morning_lane1_win_unavailable"
            )
            pre["lane1FlyProbabilityDisplayStage"] = "morning"
            pre["lane1FlyProbabilityIsProvisional"] = True
        if isinstance(final, dict):
            build_fly_prediction(final, "live_pending_v3", event_type)
        active = final if isinstance(final, dict) else race.get("prediction")
        if isinstance(active, dict) and active is not pre and active is not final:
            source = "live_pending_v3" if active.get("phase") == "final" else "morning_provisional"
            build_fly_prediction(active, source, event_type)
        if isinstance(final, dict):
            race["prediction"] = final
        elif isinstance(pre, dict):
            race["prediction"] = pre
    payload["preds"] = {
        str(int(race.get("race") or 0)): race.get("prediction")
        for race in payload.get("races") or []
    }
    return payload
