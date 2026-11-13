"""Karatsu lane-1 fly v1 probability and ten-ticket selection.

The validated fly-v1 coefficients and candidate order are frozen.  The only
production change from the archived implementation is that normal tickets are
no longer excluded from fly selection.  This cannot affect the reweighted
distribution or the lane-1 fly probability.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Iterable


MODEL_VERSION = "karatsu_lane1_fly_v1"
CONFIG_PATH = Path(__file__).with_name("karatsu_fly_v1.json")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _lane_map(race: dict) -> dict[int, dict]:
    return {
        int(row["lane"]): row
        for row in (race.get("racers") or [])
        if isinstance(row, dict) and str(row.get("lane") or "").isdigit()
    }


def _actual_course_map(race: dict) -> dict[int, int]:
    racers = _lane_map(race)
    course_by_lane = {
        lane: int(row.get("actual_course") or row.get("entry_course") or lane)
        for lane, row in racers.items()
    }
    live = race.get("live") or {}
    actual_entry = live.get("actual_entry") or (live.get("direct") or {}).get("actual_entry")
    if isinstance(actual_entry, list) and len(actual_entry) == 6:
        try:
            return {int(lane): course for course, lane in enumerate(actual_entry, start=1)}
        except (TypeError, ValueError):
            pass
    return course_by_lane


def _exhibition_map(race: dict) -> dict[int, dict]:
    entries = ((race.get("live") or {}).get("exhibition") or {}).get("entries") or []
    return {
        int(row["lane"]): row
        for row in entries
        if isinstance(row, dict) and str(row.get("lane") or "").isdigit()
    }


def _rank_score(values: dict[int, float], lane: int, *, lower_is_better: bool) -> float:
    usable = [(key, value) for key, value in values.items() if value > 0]
    if lane not in dict(usable) or len(usable) < 2:
        return 0.0
    ordered = sorted(usable, key=lambda pair: pair[1], reverse=not lower_is_better)
    rank = next(index for index, pair in enumerate(ordered) if pair[0] == lane)
    return 1.0 - 2.0 * rank / (len(ordered) - 1)


def _base_trifectas(production_prediction: dict) -> dict[str, float]:
    raw = production_prediction.get("trifectaProbabilities")
    if isinstance(raw, dict) and len(raw) == 120:
        return {str(key): _number(value) for key, value in raw.items()}
    rows = production_prediction.get("trifectaTop120")
    if isinstance(rows, list) and len(rows) == 120:
        return {
            str(row["combo"]): _number(row.get("probability")) / 100.0
            for row in rows
        }
    raise ValueError("complete_120_trifecta_distribution_required")


def _attack_score(race: dict, lane: int, course_by_lane: dict[int, int]) -> float:
    racer = _lane_map(race).get(lane, {})
    exhibition = _exhibition_map(race)
    times = {key: _number(row.get("exhibition_time")) for key, row in exhibition.items()}
    starts = {key: _number(row.get("start_time"), 0.25) for key, row in exhibition.items()}
    score = (
        0.36 * (_number(racer.get("nat_win"), 5.0) - 5.0)
        + 0.12 * (_number(racer.get("local_win"), 5.0) - 5.0)
        + 0.012 * (_number(racer.get("motor_2"), 33.0) - 33.0)
        + 0.008 * (_number(racer.get("motor_3"), 50.0) - 50.0)
        - 1.8 * (_number(racer.get("avg_st"), 0.18) - 0.18)
        + 0.22 * _rank_score(times, lane, lower_is_better=True)
        + 0.15 * _rank_score(starts, lane, lower_is_better=True)
    )
    course = course_by_lane.get(lane, lane)
    score += {2: 0.12, 3: 0.18, 4: 0.22, 5: 0.08, 6: -0.06}.get(course, 0.0)
    return score


def _wall_strength(race: dict, attack_lane: int, course_by_lane: dict[int, int]) -> float:
    attack_course = course_by_lane.get(attack_lane, attack_lane)
    if attack_course <= 2:
        return 0.0
    lane_by_course = {course: lane for lane, course in course_by_lane.items()}
    inner_lane = lane_by_course.get(attack_course - 1)
    if inner_lane is None:
        return 0.0
    return _attack_score(race, inner_lane, course_by_lane) - _attack_score(race, attack_lane, course_by_lane)


def _factor(
    combo: tuple[int, int, int],
    race: dict,
    enabled: set[str],
    coefficients: dict,
    course_by_lane: dict[int, int],
) -> float:
    head, second, third = combo
    factor = 1.0
    if "lane1_debias" in enabled and head == 1:
        factor *= _number(coefficients["lane1_debias"]["head_multiplier"], 1.0)
    if "course2_sashi_wall" in enabled:
        lane_by_course = {course: lane for lane, course in course_by_lane.items()}
        course2_lane = lane_by_course.get(2)
        if head == course2_lane:
            racer = _lane_map(race).get(head, {})
            sashi = _number(racer.get("boaters_sashi_rate"))
            factor *= 1.0 + min(coefficients["course2_sashi_wall"]["max_boost"], sashi / 100.0 * coefficients["course2_sashi_wall"]["rate_weight"])
    if "attack_and_wall" in enabled and head != 1:
        attack = _attack_score(race, head, course_by_lane)
        wall = _wall_strength(race, head, course_by_lane)
        value = coefficients["attack_and_wall"]["attack_weight"] * attack
        value -= coefficients["attack_and_wall"]["wall_weight"] * max(0.0, wall)
        factor *= math.exp(max(-0.35, min(0.35, value)))
    if "lane1_remain" in enabled and head != 1 and 1 in (second, third):
        factor *= coefficients["lane1_remain"]["multiplier"]
    if "outer_remain" in enabled and head not in (5, 6):
        outer_count = sum(lane in (5, 6) for lane in (second, third))
        factor *= coefficients["outer_remain"]["multiplier"] ** outer_count
    return factor


def reweight_trifectas(
    production_prediction: dict,
    race: dict,
    candidate: str = "combined",
    coefficients: dict | None = None,
) -> dict[str, float]:
    coefficients = coefficients or json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    enabled = set(coefficients["candidates"][candidate])
    base = _base_trifectas(production_prediction)
    course_by_lane = _actual_course_map(race)
    weighted = {}
    for combo_text, probability in base.items():
        combo = tuple(int(value) for value in combo_text.split("-"))
        weighted[combo_text] = probability * _factor(combo, race, enabled, coefficients, course_by_lane)
    total = sum(weighted.values())
    if total <= 0:
        raise ValueError("invalid_reweighted_distribution")
    return dict(sorted(((key, value / total) for key, value in weighted.items()), key=lambda pair: (-pair[1], pair[0])))


def marginals(trifectas: dict[str, float]) -> dict[str, dict[str, float]]:
    output = {role: {str(lane): 0.0 for lane in range(1, 7)} for role in ("win", "second", "third")}
    for combo, probability in trifectas.items():
        lanes = combo.split("-")
        for role, lane in zip(output, lanes):
            output[role][lane] += probability
    return output


def _ticket_rows(combos: Iterable[str], trifectas: dict[str, float], role: str) -> list[dict]:
    return [
        {"combo": combo, "probability": round(trifectas[combo] * 100.0, 4), "role": role}
        for combo in combos
    ]


def select_fly_tickets(trifectas: dict[str, float]) -> list[str]:
    """Select ten fly tickets using the frozen adjusted distribution.

    Heads other than lane 1 are ranked by their total adjusted win
    probability.  The leading head contributes eight tickets and the next
    two heads contribute one ticket each.  Ticket order inside each head is
    the adjusted trifecta-probability order already carried by ``trifectas``.
    """
    head_totals = {
        lane: sum(
            probability
            for combo, probability in trifectas.items()
            if int(combo.split("-", 1)[0]) == lane
        )
        for lane in range(2, 7)
    }
    ranked_heads = sorted(head_totals, key=lambda lane: (-head_totals[lane], lane))
    allocation = (8, 1, 1)
    selected = [
        combo
        for head, count in zip(ranked_heads[:3], allocation)
        for combo in [
            item
            for item in trifectas
            if int(item.split("-", 1)[0]) == head
        ][:count]
    ]
    if len(selected) != 10 or len(set(selected)) != 10:
        raise RuntimeError("karatsu_fly_tickets_invalid")
    return selected


def build_fly_prediction(
    production_prediction: dict,
    race: dict,
    *,
    stage: str,
    normal_tickets: Iterable[str] = (),
    allow_normal_overlap: bool = True,
    coefficients: dict | None = None,
) -> dict:
    if stage not in {"morning", "live"}:
        raise ValueError("stage_must_be_morning_or_live")
    candidate = "fly_v1"
    trifectas = reweight_trifectas(production_prediction, race, candidate, coefficients)
    probability = marginals(trifectas)
    normal = list(normal_tickets)
    normal_set = set(normal)
    if allow_normal_overlap:
        fly = select_fly_tickets(trifectas)
    else:
        # Archived comparison mode only. Production explicitly allows overlap
        # and always uses the fixed 8+1+1 allocation above.
        fly = [
            combo
            for combo in trifectas
            if not combo.startswith("1-") and combo not in normal_set
        ][:10]
        if len(fly) != 10 or len(set(fly)) != 10:
            raise RuntimeError("karatsu_fly_tickets_invalid")
    fly_probability = 1.0 - probability["win"]["1"]
    return {
        "status": "complete",
        "candidate": candidate,
        "modelVersion": MODEL_VERSION,
        "stage": stage,
        "oddsUsed": False,
        "resultsUsed": False,
        "probabilities": {
            role: {lane: round(value * 100.0, 4) for lane, value in values.items()}
            for role, values in probability.items()
        },
        "lane1FlyProbability": round(fly_probability * 100.0, 4),
        "flyPrediction": {
            "status": "complete",
            "modelVersion": MODEL_VERSION,
            "stage": stage,
            "probability": round(fly_probability * 100.0, 4),
            "overlapPolicy": "allowed" if allow_normal_overlap else "forbidden",
            "normalOverlapCount": len(normal_set.intersection(fly)),
            "tickets": _ticket_rows(fly, trifectas, "飛び"),
        },
        "trifectaProbabilities": deepcopy(trifectas),
    }
