from __future__ import annotations


def _num(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def five_head_scenario_adjustment(boats, live):
    """4攻め→5まくり差し頭／5自力外攻めを評価する。"""
    live = live or {}
    slit = live.get("slit") or {}
    exhibition_st = live.get("exhibition_st") or {}
    straight_rank = live.get("straight_rank") or {}
    exhibition_time_rank = live.get("exhibition_time_rank") or {}

    by_lane = {int(b.get("boat_no") or b.get("lane")): b for b in boats}
    lane5 = by_lane.get(5)
    lane4 = by_lane.get(4)
    if not lane5:
        return {
            "active": False,
            "condition_count": 0,
            "win_delta": 0.0,
            "scenario_bonus": 0.0,
            "course_multiplier": 0.92,
            "second_order": [4, 3, 1, 6],
            "third_order": [3, 4, 1, 6, 2],
            "reasons": ["lane5_missing"],
        }

    def state(lane):
        return str(slit.get(str(lane), slit.get(lane, "neutral")) or "neutral")

    def st(lane):
        return _num(exhibition_st.get(str(lane), exhibition_st.get(lane)))

    def rank(source, lane):
        return _num(source.get(str(lane), source.get(lane)))

    lane5_st = st(5)
    lane4_st = st(4)
    lane3_st = st(3)

    lane5_a_class = str(lane5.get("class") or "") in ("A1", "A2")
    lane5_advance = state(5) in ("advance", "ahead", "front", "前出")
    lane5_st_advantage_34 = (
        lane5_st is not None
        and (
            (lane4_st is not None and lane4_st - lane5_st >= 0.05)
            or (lane3_st is not None and lane3_st - lane5_st >= 0.05)
        )
    )
    lane5_live_power_good = (
        (rank(straight_rank, 5) is not None and rank(straight_rank, 5) <= 2)
        or (rank(exhibition_time_rank, 5) is not None and rank(exhibition_time_rank, 5) <= 2)
    )
    inside_slow = any(
        state(lane) in ("dent", "behind", "recess", "凹み")
        for lane in (1, 2)
    )
    course4_attackable = bool(
        lane4
        and (
            state(4) in ("advance", "ahead", "front", "前出", "neutral")
            or str(lane4.get("class") or "") in ("A1", "A2")
        )
    )

    score = 0.0
    win_delta = 0.0
    reasons = []

    if lane5_advance:
        score += 0.04
        win_delta += 0.02
        reasons.append("lane5_slit_advance")
    if lane5_st_advantage_34:
        score += 0.05
        win_delta += 0.02
        reasons.append("lane5_st_advantage_over_3_4")
    if lane5_a_class:
        score += 0.03
        win_delta += 0.015
        reasons.append("lane5_a_class")
    if lane5_live_power_good:
        score += 0.03
        win_delta += 0.015
        reasons.append("lane5_live_power_good")
    if inside_slow:
        score += 0.03
        win_delta += 0.01
        reasons.append("inside_1_2_slow")
    if course4_attackable:
        score += 0.02
        reasons.append("course4_attack_link")

    condition_count = sum([
        lane5_advance,
        lane5_st_advantage_34,
        lane5_a_class,
        lane5_live_power_good,
        inside_slow,
        course4_attackable,
    ])

    course_multiplier = 1.08 if condition_count >= 3 else 1.00 if condition_count >= 2 else 0.92

    return {
        "active": condition_count >= 3,
        "condition_count": condition_count,
        "win_delta": round(min(0.06, win_delta), 6),
        "scenario_bonus": round(min(0.18, score), 6),
        "course_multiplier": course_multiplier,
        "second_order": [4, 3, 1, 6],
        "third_order": [3, 4, 1, 6, 2],
        "reasons": reasons,
    }
