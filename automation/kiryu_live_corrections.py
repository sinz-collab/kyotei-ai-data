"""Validated Kiryu LIVE probability transforms.

The correction order and coefficients are frozen by the 2026-11-03..05
replay.  Both transforms affect only the second-place conditional
distribution.  Entry changes disable both transforms.
"""
from __future__ import annotations

import math

import numpy as np

from kiryu_v1_engine import A, B, C, PERMS, _softmax


MODEL_VERSION = "kiryu_live_wind_st_v1_20261106"
WIND_STRAIGHT_SECOND_COEFFICIENT = 0.150
LEFT_ST_SECOND_COEFFICIENT = 0.100
NORMAL_ENTRY = list(range(1, 7))


def _number(value):
    if value in (None, "", "-", "None"):
        return None
    try:
        parsed = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _factorize(joint):
    joint = np.asarray(joint, float)
    if joint.shape != (120,) or not np.isfinite(joint).all() or not np.isclose(joint.sum(), 1.0, atol=1e-12):
        raise RuntimeError("invalid Kiryu base 120-way probabilities")
    p1 = np.array([joint[A == lane].sum() for lane in range(6)])
    p2 = np.zeros((6, 6))
    p3 = np.zeros((6, 6, 6))
    for first in range(6):
        for second in range(6):
            if first == second:
                continue
            pair = joint[(A == first) & (B == second)].sum()
            p2[first, second] = pair / max(p1[first], 1e-30)
            for third in range(6):
                if third in (first, second):
                    continue
                index = np.flatnonzero((A == first) & (B == second) & (C == third))[0]
                p3[first, second, third] = joint[index] / max(pair, 1e-30)
    return p1, p2, p3


def _apply_second_score(joint, score):
    """Adjust P(second|first), preserving P(first) and P(third|first,second)."""
    p1, p2, p3 = _factorize(joint)
    q2 = np.zeros_like(p2)
    for first in range(6):
        allowed = np.array([lane for lane in range(6) if lane != first])
        q2[first, allowed] = _softmax(
            np.log(np.maximum(p2[first, allowed], 1e-30)) + np.asarray(score, float)[allowed]
        )
    output = p1[A] * q2[A, B] * p3[A, B, C]
    output /= output.sum()
    after = _factorize(output)
    if not np.allclose(p1, after[0], atol=1e-14, rtol=1e-12):
        raise RuntimeError("Kiryu LIVE correction changed first-place probabilities")
    if not np.allclose(p3, after[2], atol=1e-14, rtol=1e-12):
        raise RuntimeError("Kiryu LIVE correction changed third-place conditionals")
    return output


def _straight_score(boats):
    values = [_number(row.get("straight_time")) for row in boats]
    if any(value is None for value in values):
        raise RuntimeError("incomplete Kiryu LIVE straight times")
    values = np.asarray(values, float)
    scale = max(float(values.std()), 0.025)
    return np.clip((values.mean() - values) / scale, -2.0, 2.0)


def _national_st_wall(racers):
    values = [_number(row.get("avg_st")) for row in racers]
    valid = [value for value in values if value is not None]
    if not valid:
        raise RuntimeError("Kiryu LIVE national average ST is missing for all boats")
    neutral = float(np.mean(valid))
    st = np.asarray([neutral if value is None else value for value in values], float)
    wall = np.zeros(6)
    for lane in range(1, 6):
        wall[lane] = np.clip((st[lane - 1] - st[lane]) / 0.04, -1.5, 1.5)
    return st, wall


def apply_live_corrections(base_joint, racers, live_input):
    """Apply wind×straight then left-neighbor national-ST correction."""
    racers = sorted(racers, key=lambda row: int(row.get("lane") or 99))
    boats = sorted(live_input.get("boats") or [], key=lambda row: int(row.get("lane") or 99))
    if len(racers) != 6 or len(boats) != 6:
        raise RuntimeError("Kiryu LIVE correction requires six racers and six boats")
    actual_entry = live_input.get("actual_entry")
    if sorted(actual_entry or []) != NORMAL_ENTRY:
        raise RuntimeError("incomplete Kiryu LIVE actual entry")
    entry_changed = actual_entry != NORMAL_ENTRY
    national_st, wall = _national_st_wall(racers)
    straight = _straight_score(boats)
    speed = _number((live_input.get("weather") or {}).get("wind_speed"))
    if speed is None:
        raise RuntimeError("incomplete Kiryu LIVE wind speed")

    joint = np.asarray(base_joint, float).copy()
    wind_applied = False
    left_applied = False
    if not entry_changed:
        if speed <= 2.0:
            joint = _apply_second_score(joint, WIND_STRAIGHT_SECOND_COEFFICIENT * straight)
            wind_applied = True
        joint = _apply_second_score(joint, LEFT_ST_SECOND_COEFFICIENT * wall)
        left_applied = True
    if not np.isclose(joint.sum(), 1.0, atol=1e-12):
        raise RuntimeError("Kiryu LIVE corrected probabilities are not normalized")
    return joint, {
        "modelVersion": MODEL_VERSION,
        "applicationOrder": ["windStraightSecond", "leftNeighborNationalStSecond"],
        "entryChanged": entry_changed,
        "stoppedForEntryChange": entry_changed,
        "windStraight": {
            "applied": wind_applied,
            "coefficient": WIND_STRAIGHT_SECOND_COEFFICIENT,
            "condition": "wind_speed<=2m; second-place conditional only",
            "windSpeed": speed,
            "straightScore": [float(value) for value in straight],
        },
        "leftNeighborSt": {
            "applied": left_applied,
            "coefficient": LEFT_ST_SECOND_COEFFICIENT,
            "basis": "national_avg_st_100pct",
            "condition": "second-place conditional only",
            "nationalStAfterNeutralFill": [float(value) for value in national_st],
            "wall": [float(value) for value in wall],
        },
        "probabilitySum": float(joint.sum()),
    }


def normal_prediction_fields(engine, joint):
    """Format corrected normal probabilities and exact formal 6+2+2 tickets."""
    indices = engine.select_normal_indices(joint)
    win = np.array([joint[A == lane].sum() for lane in range(6)])
    second = np.array([joint[B == lane].sum() for lane in range(6)])
    third = np.array([joint[C == lane].sum() for lane in range(6)])
    pct = lambda values: {str(index + 1): round(float(value * 100), 10) for index, value in enumerate(values)}
    tickets = [{
        "combo": "-".join(str(value + 1) for value in PERMS[index]),
        "probability": round(float(joint[index] * 100), 8),
    } for index in indices]
    return {
        "win": pct(win),
        "second": pct(second),
        "third": pct(third),
        "ai": tickets[:6],
        "balance": tickets[6:8],
        "aiUpset": tickets[8:10],
        "tickets": tickets,
    }
