"""Research-only Ashiya v5.1 lap/turn conditional adjustment.

The coefficients in the final instruction are candidates, not production
parameters.  This module therefore exposes the exact calculation and audit,
but callers must opt in explicitly.  The default production mode is disabled.
"""

from __future__ import annotations

import math
from itertools import permutations

from .utils import as_float, as_int, normalize


RESEARCH_COEFFICIENTS = {
    "lap": {"second_attack": 0.14, "third_attack": 0.06},
    "turn": {"second_chase": 0.02, "third_chase": 0.18},
    "combined": {
        "second_attack": 0.10,
        "second_chase": 0.02,
        "third_attack": 0.06,
        "third_chase": 0.13,
    },
}


def _clip(value, low, high):
    return min(high, max(low, value))


def signal(delta):
    """Research signal: clip((delta - .10) / .15, 0, 2)."""
    return _clip((delta - 0.10) / 0.15, 0.0, 2.0)


def build_signals(racers):
    """Compare each actual course 2-6 with its adjacent inside course."""
    by_course = {}
    for row in racers:
        course = as_int(row.get("actual_course") or row.get("entry_course"), as_int(row.get("lane")))
        if not 1 <= course <= 6 or course in by_course:
            return None, "actual_entry_incomplete"
        by_course[course] = row
    if set(by_course) != set(range(1, 7)):
        return None, "actual_entry_incomplete"

    rows = {}
    for course in range(1, 7):
        racer = by_course[course]
        lane = as_int(racer.get("lane"))
        row = {
            "lane": lane,
            "actual_course": course,
            "lap_delta": None,
            "turn_delta": None,
            "lap": 0.0,
            "turn": 0.0,
            "attack": 0.0,
            "chase": 0.0,
            "status": "inner_course" if course == 1 else "missing_time",
        }
        if course >= 2:
            inside = by_course[course - 1]
            target_lap = as_float(racer.get("lap_time"), 0.0)
            inside_lap = as_float(inside.get("lap_time"), 0.0)
            target_turn = as_float(racer.get("turn_time"), 0.0)
            inside_turn = as_float(inside.get("turn_time"), 0.0)
            if target_lap > 0 and inside_lap > 0 and target_turn > 0 and inside_turn > 0:
                lap_delta = inside_lap - target_lap
                turn_delta = inside_turn - target_turn
                lap = signal(lap_delta)
                turn = signal(turn_delta)
                row.update({
                    "lap_delta": round(lap_delta, 6),
                    "turn_delta": round(turn_delta, 6),
                    "lap": round(lap, 8),
                    "turn": round(turn, 8),
                    "attack": round(lap * _clip((turn + 0.5) / 1.5, 0.0, 1.0), 8),
                    "chase": round(turn * _clip((lap + 0.5) / 1.5, 0.0, 1.0), 8),
                    "status": "ok",
                })
        rows[lane] = row
    return rows, None


def _place_adjustment(values, head, shifts):
    raw = [0.0 if lane == head else values[lane - 1] * math.exp(shifts.get(lane, 0.0)) for lane in range(1, 7)]
    total = sum(raw)
    if total <= 0:
        return [0.0 if lane == head else 0.2 for lane in range(1, 7)]
    return [value / total for value in raw]


def apply_to_head_conditionals(head_conditionals, racers, mode="disabled"):
    """Return adjusted copies plus audit; disabled never changes probabilities."""
    signals, reason = build_signals(racers)
    audit = {
        "model": "ashiya_v51_exhibition_research",
        "mode": mode,
        "productionAdopted": False,
        "coefficients": RESEARCH_COEFFICIENTS.get(mode),
        "signals": signals or {},
        "status": "disabled_unvalidated" if mode == "disabled" else "pending",
    }
    if reason:
        audit.update({"status": "fallback", "reason": reason})
        return head_conditionals, audit
    if mode == "disabled":
        audit["reason"] = "formal_coefficient_validation_unavailable"
        return head_conditionals, audit
    if mode not in RESEARCH_COEFFICIENTS:
        audit.update({"status": "fallback", "reason": "unknown_research_mode"})
        return head_conditionals, audit

    coefficients = RESEARCH_COEFFICIENTS[mode]
    second_shifts = {}
    third_shifts = {}
    for lane, row in signals.items():
        attack = row["attack"]
        chase = row["chase"]
        second_shifts[lane] = (
            coefficients.get("second_attack", 0.0) * attack
            + coefficients.get("second_chase", 0.0) * chase
        )
        third_shifts[lane] = (
            coefficients.get("third_attack", 0.0) * attack
            + coefficients.get("third_chase", 0.0) * chase
        )

    adjusted = {}
    for head in range(1, 7):
        source = head_conditionals.get(head) or head_conditionals.get(str(head))
        if not source:
            audit.update({"status": "fallback", "reason": "head_conditionals_missing"})
            return head_conditionals, audit
        adjusted[head] = {
            **source,
            "second": _place_adjustment(source["second"], head, second_shifts),
            "third": _place_adjustment(source["third"], head, third_shifts),
        }
    audit.update({
        "status": "research_applied",
        "secondLogShifts": {str(k): round(v, 8) for k, v in second_shifts.items()},
        "thirdLogShifts": {str(k): round(v, 8) for k, v in third_shifts.items()},
    })
    return adjusted, audit


def enumerate_joint(win, head_conditionals):
    """Enumerate all 120 trifectas with exact sequential exclusion."""
    rows = []
    for a, b, c in permutations(range(1, 7), 3):
        conditional = head_conditionals.get(a) or head_conditionals.get(str(a))
        second = conditional["second"]
        third = conditional["third"]
        second_denominator = sum(second[lane - 1] for lane in range(1, 7) if lane != a)
        third_denominator = sum(third[lane - 1] for lane in range(1, 7) if lane not in (a, b))
        probability = (
            win[a - 1]
            * second[b - 1] / max(1e-15, second_denominator)
            * third[c - 1] / max(1e-15, third_denominator)
        )
        rows.append({"combination": f"{a}-{b}-{c}", "probability": probability})
    total = sum(row["probability"] for row in rows)
    first_marginals = {
        str(head): sum(row["probability"] for row in rows if row["combination"].startswith(f"{head}-"))
        for head in range(1, 7)
    }
    return rows, {
        "combinationCount": len(rows),
        "sum": total,
        "firstMarginals": first_marginals,
        "sumValid": abs(total - 1.0) < 1e-10,
        "firstMarginalsValid": all(abs(first_marginals[str(head)] - win[head - 1]) < 1e-10 for head in range(1, 7)),
    }
