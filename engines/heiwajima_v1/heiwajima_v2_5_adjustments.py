from __future__ import annotations

import math


def _num(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        parsed = float(value)
        return default if math.isnan(parsed) else parsed
    except (TypeError, ValueError):
        return default


def _clip(value, low, high):
    return max(low, min(high, value))


COURSE_LOCAL_CAP = {1: 0.025, 2: 0.035, 3: 0.045, 4: 0.055, 5: 0.075, 6: 0.085}
MOTOR_RECENT_CAP = {1: 0.045, 2: 0.050, 3: 0.055, 4: 0.065, 5: 0.075, 6: 0.085}
OUTER_BREAK_CAP = {4: 0.070, 5: 0.105, 6: 0.135}


def course_local_interaction(boat: dict, course: int) -> dict:
    cap = COURSE_LOCAL_CAP.get(int(course), 0.03)
    local_win = _num(boat.get("local_win_score"))
    local_2 = _num(boat.get("local_2_rate"))
    local_3 = _num(boat.get("local_3_rate"))
    local_st = _num(boat.get("local_st"))
    nat_win = _num(boat.get("nat_win_score"))
    klass = str(boat.get("class") or "B1")

    score = 0.0
    evidence = 0
    if local_win is not None and local_win > 0.0:
        score += _clip((local_win - 5.50) / 2.00, -1.0, 1.0) * 0.50
        evidence += 1
    if local_3 is not None and local_3 > 0.0:
        score += _clip((local_3 - 50.0) / 25.0, -1.0, 1.0) * 0.22
        evidence += 1
    if local_2 is not None and local_2 > 0.0:
        score += _clip((local_2 - 33.0) / 22.0, -1.0, 1.0) * 0.13
        evidence += 1
    if local_st is not None:
        score += _clip((0.17 - local_st) / 0.05, -1.0, 1.0) * 0.28
        evidence += 1
    if nat_win is not None and nat_win > 0.0 and klass in ("A1", "A2"):
        score += _clip((nat_win - 5.80) / 1.80, -0.5, 1.0) * 0.12

    if evidence == 0:
        return {"win": 0.0, "second": 0.0, "third": 0.0, "strength": 0.0, "evidence": 0}

    win = _clip(score * cap, -cap * 0.70, cap)
    return {
        "win": win,
        "second": _clip(win * 0.45, -0.025, 0.040),
        "third": _clip(win * 0.30, -0.020, 0.030),
        "strength": score,
        "evidence": evidence,
    }


def motor_recent_adjustment(boat: dict, course: int, day_bucket: str) -> dict:
    recent = boat.get("motor_recent") or {}
    if not recent or not bool(recent.get("available")):
        return {"win": 0.0, "second": 0.0, "third": 0.0, "score": 0.0}

    top2 = _num(recent.get("top2_rate"))
    top3 = _num(recent.get("top3_rate"))
    rank = _num(recent.get("avg_exhibition_rank"))
    trend = str(recent.get("trend") or "flat").lower()

    score = 0.0
    if top2 is not None:
        score += _clip((top2 - 33.0) / 30.0, -1.2, 1.2) * 0.45
    if top3 is not None:
        score += _clip((top3 - 50.0) / 35.0, -1.2, 1.2) * 0.35
    if rank is not None:
        score += _clip((4.0 - rank) / 2.5, -1.0, 1.0) * 0.10
    if trend == "up":
        score += 0.18
    elif trend == "down":
        score -= 0.18

    stage_mult = {"early": 1.15, "middle": 1.00, "late": 0.80}.get(day_bucket, 1.0)
    cap = MOTOR_RECENT_CAP.get(int(course), 0.055)
    win = _clip(score * 0.055 * stage_mult, -cap, cap)
    return {
        "win": win,
        "second": _clip(win * 0.78, -0.055, 0.055),
        "third": _clip(win * 0.86, -0.060, 0.060),
        "score": score,
    }


def original_exhibition_composite(exhibition: dict, live_mult: float, cap: float) -> dict:
    lap = _num(exhibition.get("lap_score"), 0.0) or 0.0
    turn = _num(exhibition.get("turn_score"), 0.0) or 0.0
    straight = _num(exhibition.get("straight_score"), 0.0) or 0.0
    summ = _num(exhibition.get("sum_score"), 0.0) or 0.0
    raw = lap * 0.036 + turn * 0.036 + straight * 0.030 + summ * 0.018
    composite = _clip(raw * live_mult, -cap, cap)
    return {
        "win": composite,
        "second": composite * 0.80,
        "third": composite * 0.72,
        "raw": raw,
        "lap": lap,
        "turn": turn,
        "straight": straight,
        "sum": summ,
    }


def outer_break_adjustment(boat, course, live, slit_adjustment, exhibition, motor_power, day_bucket):
    course = int(course)
    if course not in (4, 5, 6):
        return {"win": 0.0, "second": 0.0, "third": 0.0, "signals": []}

    signals = []
    klass = str(boat.get("class") or "B1")
    nat_win = _num(boat.get("nat_win_score"), 0.0) or 0.0
    local_win = _num(boat.get("local_win_score"), 0.0) or 0.0

    if klass == "A1" or (klass == "A2" and max(nat_win, local_win) >= 5.8):
        signals.append("player_strength")

    recent = boat.get("motor_recent") or {}
    recent_top3 = _num(recent.get("top3_rate"), 0.0) or 0.0
    recent_top2 = _num(recent.get("top2_rate"), 0.0) or 0.0
    trend = str(recent.get("trend") or "flat").lower()
    if motor_power >= 0.55 or recent_top3 >= 60.0 or recent_top2 >= 45.0 or trend == "up":
        signals.append("motor")

    if float(slit_adjustment.get("attack", 0.0)) >= 0.045 or float(slit_adjustment.get("win", 0.0)) >= 0.050:
        signals.append("start_slit")

    orig_strength = (
        (_num(exhibition.get("lap_score"), 0.0) or 0.0) * 0.30
        + (_num(exhibition.get("turn_score"), 0.0) or 0.0) * 0.30
        + (_num(exhibition.get("straight_score"), 0.0) or 0.0) * 0.25
        + (_num(exhibition.get("sum_score"), 0.0) or 0.0) * 0.15
    )
    if orig_strength >= 0.35:
        signals.append("original_exhibition")

    slit = live.get("slit") or {}
    for inner in range(1, course):
        state = str(slit.get(str(inner), slit.get(inner, "neutral")) or "neutral")
        if state in ("dent", "behind", "recess", "凹み"):
            if course - inner <= 2 or (course == 6 and inner == 4):
                signals.append("inside_dent")
                break

    if len(signals) < 3:
        return {"win": 0.0, "second": 0.0, "third": 0.0, "signals": signals}

    step = {4: 0.030, 5: 0.040, 6: 0.048}[course]
    boost = min(OUTER_BREAK_CAP[course], step * (len(signals) - 2))
    if day_bucket == "late":
        boost *= 0.85

    return {
        "win": boost,
        "second": min(0.055, boost * 0.52),
        "third": min(0.070, boost * 0.68),
        "signals": signals,
    }
