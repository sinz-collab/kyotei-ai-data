"""Frozen Karatsu normal-ticket conditional-second correction.

The feature order, standardization statistics and logistic-regression
parameters are an exact export of the research pipeline trained only on races
before 2026-09-18.  Runtime selection accepts the existing v1 ten tickets as
an input and never uses odds or results.
"""
from __future__ import annotations

from collections import Counter
from itertools import permutations
import json
import math
from pathlib import Path
from typing import Any, Iterable


MODEL_PATH = Path(__file__).with_name("karatsu_conditional_second_v1.json")
MODEL_VERSION = "karatsu_conditional_second_v1"
COMBOS = tuple("-".join(map(str, value)) for value in permutations(range(1, 7), 3))


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _lane_map(racers: Iterable[Any]) -> dict[int, Any]:
    output: dict[int, Any] = {}
    for racer in racers:
        lane = int(getattr(racer, "lane", 0) or 0)
        if lane in range(1, 7):
            output[lane] = racer
    if sorted(output) != list(range(1, 7)):
        raise ValueError("karatsu_conditional_requires_six_lanes")
    return output


def feature_vector(racers: Iterable[Any], head: int, second: int, wind: float) -> list[float]:
    """Build the 19 research features in their frozen order."""
    by_lane = _lane_map(racers)
    first = by_lane[head]
    runner_up = by_lane[second]
    head_power = _number(getattr(first, "nat_win", 0.0))
    second_power = _number(getattr(runner_up, "nat_win", 0.0))
    head_motor = _number(getattr(first, "motor_2", 0.0))
    second_motor = _number(getattr(runner_up, "motor_2", 0.0))
    second_local = _number(getattr(runner_up, "local_win", 0.0))
    head_local = _number(getattr(first, "local_win", 0.0))
    wind = _number(wind)
    return [
        float(head), float(second), float(head == 1), float(second == 1),
        float(abs(second - head) == 1), head_power, second_power,
        second_power - head_power, head_motor, second_motor,
        second_motor - head_motor, second_local, head_local, wind,
        wind * float(head >= 3), wind * float(second >= 4), float(head >= 3),
        float(second == 1), float(head >= 3 and second == 1),
    ]


def _positive_probability(vector: list[float], model: dict) -> float:
    standardized = [
        (value - mean) / scale
        for value, mean, scale in zip(vector, model["scaler_mean"], model["scaler_scale"])
    ]
    decision = model["intercept"] + sum(
        value * coefficient
        for value, coefficient in zip(standardized, model["coefficient"])
    )
    if decision >= 0:
        return 1.0 / (1.0 + math.exp(-decision))
    exp_value = math.exp(decision)
    return exp_value / (1.0 + exp_value)


def conditional_second_probabilities(
    racers: Iterable[Any], wind: float, model: dict | None = None
) -> dict[tuple[int, int], float]:
    model = model or json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    output: dict[tuple[int, int], float] = {}
    for head in range(1, 7):
        seconds = [lane for lane in range(1, 7) if lane != head]
        raw = [
            _positive_probability(feature_vector(racers, head, second, wind), model)
            for second in seconds
        ]
        total = sum(raw)
        if total <= 0:
            raise RuntimeError("karatsu_conditional_probability_invalid")
        output.update({(head, second): value / total for second, value in zip(seconds, raw)})
    return output


def raw_trifecta_scores(
    first: dict[int, float], second: dict[int, float], third: dict[int, float]
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for combo in COMBOS:
        head, runner_up, third_lane = map(int, combo.split("-"))
        win = max(_number(first.get(head)), 1e-7)
        place2 = max(_number(second.get(runner_up)), 1e-7)
        place3 = max(_number(third.get(third_lane)), 1e-7)
        scores[combo] = (
            win
            * place2 / max(1.0 - win, 0.05)
            * place3 / max(1.0 - _number(third.get(head)) - _number(third.get(runner_up)), 0.05)
        )
    return scores


def select_normal_tickets(
    base_tickets: Iterable[str],
    first: dict[int, float],
    second: dict[int, float],
    third: dict[int, float],
    racers: Iterable[Any],
    wind: float,
    *,
    live_wind_eligible: bool,
    model: dict | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Apply the exact one-ticket research correction to a v1 ticket list."""
    model = model or json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    original = list(base_tickets)
    if len(original) != 10 or len(set(original)) != 10:
        raise ValueError("karatsu_v1_normal_tickets_must_be_ten_unique")

    raw_scores = raw_trifecta_scores(first, second, third)
    predicted = conditional_second_probabilities(racers, wind, model)
    head_totals = {
        head: sum(score for combo, score in raw_scores.items() if combo.startswith(f"{head}-"))
        for head in range(1, 7)
    }
    base_conditional = {
        (head, second_lane): sum(
            score for combo, score in raw_scores.items()
            if combo.startswith(f"{head}-{second_lane}-")
        ) / head_totals[head]
        for head in range(1, 7)
        for second_lane in range(1, 7)
        if second_lane != head
    }
    low, high = model["conditional_ratio_clip"]
    power = model["conditional_ratio_power"]
    adjusted_scores: dict[str, float] = {}
    for combo, score in raw_scores.items():
        head, second_lane, _ = map(int, combo.split("-"))
        ratio = predicted[(head, second_lane)] / max(base_conditional[(head, second_lane)], 0.001)
        adjusted_scores[combo] = score * max(low, min(high, ratio)) ** power

    candidate = original.copy()
    heads = {combo[0] for combo in original}
    counts = Counter(combo[0] for combo in candidate)
    droppable = [combo for combo in candidate if counts[combo[0]] > 1]
    drop = min(droppable, key=adjusted_scores.__getitem__)
    pick = max(
        (combo for combo in COMBOS if combo not in candidate and combo[0] in heads),
        key=adjusted_scores.__getitem__,
    )
    replaced = False
    if adjusted_scores[pick] > model["replacement_threshold"] * adjusted_scores[drop]:
        candidate[candidate.index(drop)] = pick
        replaced = True

    protected = set(
        sorted(original, key=raw_scores.__getitem__, reverse=True)[: model["protected_original_count"]]
    )
    for missing in [combo for combo in protected if combo not in candidate]:
        added = [combo for combo in candidate if combo not in original and combo not in protected]
        if not added:
            break
        counts = Counter(combo[0] for combo in candidate)
        added = [combo for combo in added if counts[combo[0]] >= 2 or combo[0] == missing[0]]
        if not added:
            break
        remove = min(added, key=raw_scores.__getitem__)
        candidate[candidate.index(remove)] = missing

    active = bool(live_wind_eligible and _number(wind) >= 5.0)
    final = candidate if active else original
    if len(final) != 10 or len(set(final)) != 10:
        raise RuntimeError("karatsu_conditional_output_invalid")
    return final, {
        "modelVersion": MODEL_VERSION,
        "active": active,
        "liveWindEligible": bool(live_wind_eligible),
        "windSpeed": _number(wind),
        "candidateChanged": candidate != original,
        "replaced": replaced,
        "baseTickets": original,
        "candidateTickets": candidate,
    }
