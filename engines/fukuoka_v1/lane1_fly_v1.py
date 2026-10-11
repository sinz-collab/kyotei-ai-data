from __future__ import annotations

import json
import math
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


COEFFICIENTS_PATH = Path(__file__).with_name("fukuoka_fly_candidate_coefficients.json")


def _load_coefficients() -> dict[str, Any]:
    coefficients = json.loads(COEFFICIENTS_PATH.read_text(encoding="utf-8"))
    required = {
        "f1": ("prior_escape_probability", "pseudo_starts"),
        "calibration": ("intercept", "f1_logit_coef", "tide60_coef"),
    }
    for section, keys in required.items():
        values = coefficients.get(section)
        if not isinstance(values, dict) or any(key not in values for key in keys):
            raise RuntimeError(f"fukuoka_lane1_fly_coefficients_invalid:{section}")
    return coefficients


COEFFICIENTS = _load_coefficients()
MODEL_NAME = str(COEFFICIENTS["name"])


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def calculate_base_fly(escape_rate: Any, attack_starts: Any) -> float | None:
    rate = _number(escape_rate)
    starts = _number(attack_starts)
    if rate is None or starts is None or not 0.0 <= rate <= 100.0 or starts < 0.0:
        return None
    f1 = COEFFICIENTS["f1"]
    prior = float(f1["prior_escape_probability"])
    pseudo_starts = float(f1["pseudo_starts"])
    smoothed_escape = (starts * rate / 100.0 + pseudo_starts * prior) / (
        starts + pseudo_starts
    )
    return 1.0 - smoothed_escape


def calculate_probability(base_fly: Any, tide60: Any) -> float | None:
    base = _number(base_fly)
    tide = _number(tide60)
    if base is None or not 0.0 < base < 1.0 or tide not in (0.0, 1.0):
        return None
    calibration = COEFFICIENTS["calibration"]
    linear = (
        float(calibration["intercept"])
        + float(calibration["f1_logit_coef"]) * math.log(base / (1.0 - base))
        + float(calibration["tide60_coef"]) * tide
    )
    return 1.0 / (1.0 + math.exp(-linear))


def tide60_indicator(payload: dict, deadline: Any) -> tuple[int | None, dict[str, Any]]:
    try:
        target = datetime.strptime(str(deadline), "%H:%M")
    except (TypeError, ValueError):
        return None, {"status": "unavailable", "reason": "deadline_missing_or_invalid"}

    tide = payload.get("tide")
    events = tide.get("events") if isinstance(tide, dict) else None
    if not isinstance(events, list) or not events:
        return None, {"status": "unavailable", "reason": "tide_events_missing"}

    valid_events = []
    matched_events = []
    for event in events:
        if not isinstance(event, dict):
            continue
        label = str(event.get("type") or "")
        if "満潮" not in label and "干潮" not in label:
            continue
        try:
            when = datetime.strptime(str(event.get("time") or ""), "%H:%M")
        except (TypeError, ValueError):
            continue
        minutes = int(abs((when - target).total_seconds()) // 60)
        normalized = {
            "time": when.strftime("%H:%M"),
            "type": "high" if "満潮" in label else "low",
            "minutesFromDeadline": minutes,
        }
        valid_events.append(normalized)
        if minutes <= 60:
            matched_events.append(normalized)

    if not valid_events:
        return None, {"status": "unavailable", "reason": "valid_tide_events_missing"}
    return int(bool(matched_events)), {
        "status": "available",
        "deadline": target.strftime("%H:%M"),
        "windowMinutesInclusive": 60,
        "indicator": int(bool(matched_events)),
        "matchedEvents": matched_events,
    }


def _level(probability_percent: float) -> str:
    if probability_percent >= 60.0:
        return "危険"
    if probability_percent >= 50.0:
        return "警戒"
    if probability_percent >= 40.0:
        return "注意"
    return "通常"


def _record_only(race: dict, race_input: dict | None) -> dict[str, Any]:
    racers = {
        int(row.get("lane")): row
        for row in race.get("racers") or []
        if isinstance(row, dict) and str(row.get("lane") or "").isdigit()
    }
    attack = {}
    for lane in range(2, 7):
        racer = racers.get(lane) or {}
        attack[str(lane)] = {
            "sashiRate": _number(racer.get("boaters_sashi_rate")),
            "makuriRate": _number(racer.get("boaters_makuri_rate")),
            "makuriSashiRate": _number(
                racer.get("boaters_makuri_sashi_rate")
                if racer.get("boaters_makuri_sashi_rate") is not None
                else racer.get("boaters_makurizashi_rate")
            ),
        }
    engine_input = race_input or {}
    live_direct = ((race.get("live") or {}).get("direct") or {})
    return {
        "windDirection": (
            engine_input.get("wind_direction")
            or live_direct.get("wind_direction")
            or None
        ),
        "windSpeed": _number(
            engine_input.get("wind_speed")
            if engine_input.get("wind_speed") is not None
            else live_direct.get("wind_speed")
        ),
        "attack": attack,
        "wall": deepcopy(attack.get("2")),
        "appliedToProbability": False,
    }


def predict_lane1_fly(
    payload: dict,
    race: dict,
    stage: str,
    race_input: dict | None = None,
) -> dict[str, Any]:
    lane1 = next(
        (
            row
            for row in race.get("racers") or []
            if isinstance(row, dict) and str(row.get("lane")) == "1"
        ),
        None,
    )
    escape_rate = _number((lane1 or {}).get("boaters_escape_rate"))
    attack_starts = _number((lane1 or {}).get("boaters_kimarite_starts"))
    base_fly = calculate_base_fly(escape_rate, attack_starts)
    tide60, tide_detail = tide60_indicator(payload, race.get("deadline"))

    missing = []
    if lane1 is None:
        missing.append("lane1_racer")
    if escape_rate is None:
        missing.append("boaters_escape_rate")
    if attack_starts is None:
        missing.append("boaters_kimarite_starts")
    if tide60 is None:
        missing.append("tide_events_or_deadline")

    detail: dict[str, Any] = {
        "status": "unavailable" if missing else "computed",
        "model": MODEL_NAME,
        "stage": stage,
        "calculationOrder": [
            "f1_20_start_smoothing",
            "f1_logit",
            "logistic_recalibration",
            "tide60_logit_adjustment",
        ],
        "inputs": {
            "escapeRatePercent": escape_rate,
            "attackStarts": attack_starts,
            "tide60": tide60,
        },
        "baseFly": base_fly,
        "tide": tide_detail,
        "coefficients": {
            "priorEscapeProbability": COEFFICIENTS["f1"]["prior_escape_probability"],
            "pseudoStarts": COEFFICIENTS["f1"]["pseudo_starts"],
            "intercept": COEFFICIENTS["calibration"]["intercept"],
            "f1LogitCoef": COEFFICIENTS["calibration"]["f1_logit_coef"],
            "tide60Coef": COEFFICIENTS["calibration"]["tide60_coef"],
        },
        "recordOnly": _record_only(race, race_input),
        "oddsUsedForPrediction": False,
        "resultUsedForPrediction": False,
        "existingP1UsedForPrediction": False,
        "liveP1DeltaApplied": False,
    }

    probability = calculate_probability(base_fly, tide60)
    if missing or probability is None:
        detail["missingInputs"] = missing or ["calculation_input_invalid"]
        detail["probability"] = None
        return {
            "lane1FlyProbability": None,
            "lane1FlyLevel": "算出不可",
            "lane1FlyStage": stage,
            "lane1FlyDetail": detail,
        }

    probability_percent_raw = probability * 100.0
    probability_percent = round(probability_percent_raw, 2)
    detail["probabilityRaw"] = probability_percent_raw
    detail["probability"] = probability_percent
    return {
        "lane1FlyProbability": probability_percent,
        "lane1FlyLevel": _level(probability_percent),
        "lane1FlyStage": stage,
        "lane1FlyDetail": detail,
    }
