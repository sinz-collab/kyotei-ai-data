from __future__ import annotations

from itertools import permutations
from typing import Any

BASE_WIN_PERCENT = {1: 53.35, 2: 14.48, 3: 14.98, 4: 8.70, 5: 4.99, 6: 3.50}
BASE_CUT_POINTS = ((0.0, 0.0), (5.0, 4.0), (8.0, 8.0), (11.0, 14.0), (14.0, 20.0), (16.0, 24.0))

def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, "", "-"):
            return default
        return float(v)
    except Exception:
        return default

def _normalize(values: dict[int, float], excluded: set[int] | None = None) -> dict[int, float]:
    excluded = excluded or set()
    raw = {k: (0.0 if k in excluded else max(float(v), 1e-9)) for k, v in values.items()}
    total = sum(raw.values())
    return {k: (0.0 if k in excluded else raw[k] / total) for k in raw}

def _finish_values(runs: list[dict]) -> list[int]:
    values = []
    for run in runs or []:
        text = str(run.get("finish") or "").replace("着", "")
        if text.isdigit() and int(text) in range(1, 7):
            values.append(int(text))
    return values

def _rank_low(value: float, values: list[float]) -> int:
    return 1 + sum(other < value for other in values)

def _rank_high(value: float, values: list[float]) -> int:
    return 1 + sum(other > value for other in values)

def interpolate_base_cut(weakness_score: float) -> float:
    weakness = max(0.0, min(16.0, float(weakness_score)))
    for (left_x, left_y), (right_x, right_y) in zip(BASE_CUT_POINTS, BASE_CUT_POINTS[1:]):
        if weakness <= right_x:
            ratio = (weakness - left_x) / (right_x - left_x)
            return left_y + (right_y - left_y) * ratio
    return BASE_CUT_POINTS[-1][1]

def course_attack_score(course: int, sashi: float, makuri: float, makuri_sashi: float) -> float:
    if course == 2:
        if sashi >= 25.0: return 4.0
        if sashi >= 15.0: return 3.0
        if sashi >= 8.0: return 0.5
        if sashi > 0.0: return -1.0
        return -3.0
    attack = makuri + makuri_sashi
    if course == 3:
        if attack >= 25.0: return 4.0
        if attack >= 15.0: return 3.0
        if attack >= 8.0: return 2.0
        if attack > 0.0: return 0.5
        return -2.0
    if attack >= 20.0: return 4.0
    if attack >= 10.0: return 3.0
    if attack >= 5.0: return 1.0
    return -2.0

def _by_course(features: dict[int, dict]) -> dict[int, int]:
    return {int(row["actual_course"]): lane for lane, row in features.items()}


def calculate_weakness_score(features: dict[int, dict], attack_pressure: float) -> tuple[float, float, float]:
    inside = features[_by_course(features)[1]]
    escape = inside["escape"]
    if escape < 30.0: static = 5.0
    elif escape < 40.0: static = 4.0
    elif escape < 50.0: static = 2.5
    elif escape < 60.0: static = 1.0
    else: static = 0.0

    motor_values = [features[lane]["motor_rate_top2"] for lane in range(1, 7)]
    motor_rank = _rank_high(inside["motor_rate_top2"], motor_values)
    if motor_rank >= 5: static += 2.5
    elif motor_rank >= 4: static += 1.5

    recent = inside["recent_finishes"][-3:]
    if recent:
        average = sum(recent) / len(recent)
        if average >= 4.5: static += 2.0
        elif average >= 3.5: static += 1.0

    live = 0.0
    for rank in (
        inside["exhibition_time_rank"], inside["start_rank"],
        inside["lap_rank"], inside["turn_rank"],
    ):
        if rank >= 5 and rank < 90: live += 2.0
        elif rank == 4: live += 1.0

    weakness = (static * 0.4 + live * 0.3 + attack_pressure * 0.3) * 2.4
    return max(0.0, min(16.0, weakness)), static, live

def calculate_head_scores(features: dict[int, dict], morning_win: dict[int, float]) -> dict[int, float]:
    by_course = _by_course(features)
    inside = features[by_course[1]]
    scores = {}
    for course in (2, 3, 4):
        lane = by_course[course]
        row = features[lane]
        morning = morning_win[lane]
        if morning >= 18.0: score = 3.0
        elif morning >= 15.0: score = 2.0
        elif morning >= 12.0: score = 1.0
        else: score = 0.0

        national_diff = row["national"] - inside["national"]
        if national_diff >= 0.7: score += 2.0
        elif national_diff >= 0.3: score += 1.0

        if row["local"] > 0.0 and inside["local"] > 0.0:
            local_diff = row["local"] - inside["local"]
            if local_diff >= 0.7: score += 2.0
            elif local_diff >= 0.3: score += 1.0

        motor2_diff = row["motor_rate_top2"] - inside["motor_rate_top2"]
        if motor2_diff >= 10.0: score += 2.0
        elif motor2_diff >= 5.0: score += 1.0

        motor3_diff = row["motor_rate_top3"] - inside["motor_rate_top3"]
        if motor3_diff >= 12.0: score += 2.0
        elif motor3_diff >= 6.0: score += 1.0

        recent = row["recent_finishes"][-3:]
        if 1 in recent: score += 1.0
        if sum(value <= 2 for value in recent) >= 2: score += 1.0

        if row["start_rank"] <= 2: score += 2.0
        elif row["start_rank"] == 3: score += 1.0
        if row["exhibition_time_rank"] <= 2: score += 1.0
        if row["lap_rank"] <= 2: score += 1.0
        if row["turn_rank"] <= 2: score += 2.0
        score += course_attack_score(course, row["sashi"], row["makuri"], row["makuri_sashi"])
        scores[lane] = score
    return scores

def head_order_and_pressure(head_scores: dict[int, float]) -> tuple[list[int], float, float, float]:
    order = sorted(head_scores, key=lambda lane: (-head_scores[lane], lane))
    top, second, third = order
    gap = head_scores[top] - head_scores[second]
    top_score = head_scores[top]
    if top_score >= 12.0: pressure = 4.0
    elif top_score >= 10.0: pressure = 3.0
    elif top_score >= 8.0: pressure = 2.0
    else: pressure = 0.0
    if gap >= 4.0: pressure += 2.0
    elif gap >= 2.5: pressure += 1.0
    dominance = top_score - ((head_scores[second] + head_scores[third]) / 2.0)
    return order, gap, pressure, dominance

def redistribution_weights(gap: float) -> tuple[float, float, float]:
    if gap >= 4.0: return (0.80, 0.15, 0.05)
    if gap >= 2.5: return (0.65, 0.25, 0.10)
    return (0.50, 0.35, 0.15)

def redistribute_base_cut(
    values: dict[int, float], base_cut: float, head_scores: dict[int, float],
    order: list[int], weights: tuple[float, float, float],
) -> dict[int, float]:
    redistributed = dict(values)
    returned = 0.0
    for lane, weight in zip(order, weights):
        amount = base_cut * weight
        if head_scores[lane] <= 0.0:
            returned += amount
        else:
            redistributed[lane] += amount
    redistributed[order[0]] += returned
    return redistributed

def build_variable_p1(
    features: dict[int, dict], morning_win: dict[int, float],
    current_win: dict[int, float] | None = None,
) -> tuple[dict[int, float], dict[str, Any]]:
    by_course = _by_course(features)
    inside_lane = by_course[1]
    head_scores = calculate_head_scores(features, morning_win)
    order, gap, attack_pressure, dominance = head_order_and_pressure(head_scores)
    weakness, static_weakness, live_weakness = calculate_weakness_score(features, attack_pressure)
    base_cut = interpolate_base_cut(weakness)
    values = {
        lane: BASE_WIN_PERCENT[row["actual_course"]]
        for lane, row in features.items()
    }
    values[inside_lane] -= base_cut

    weights = redistribution_weights(gap)
    values = redistribute_base_cut(values, base_cut, head_scores, order, weights)

    current_win = current_win or {lane: features[lane]["p1"] for lane in range(1, 7)}
    for lane in range(1, 7):
        values[lane] += (current_win[lane] - morning_win[lane]) * 0.35

    takeover = (
        weakness >= 12.0
        and head_scores[order[0]] >= 12.0
        and gap >= 4.0
        and dominance >= 6.0
    )
    if takeover:
        values[inside_lane] -= 6.0
        values[order[0]] += 6.0

    normalized = _normalize(values)
    return normalized, {
        "weakness_score": weakness,
        "static_weakness": static_weakness,
        "live_weakness": live_weakness,
        "attack_pressure": attack_pressure,
        "base_cut": base_cut,
        "head_scores": head_scores,
        "head_order": order,
        "head_gap": gap,
        "head_dominance": dominance,
        "redistribution": weights,
        "takeover": takeover,
        "takeover_lane": order[0] if takeover else None,
        "actual_course_by_lane": {
            lane: features[lane]["actual_course"] for lane in range(1, 7)
        },
        "entry_changed": any(
            features[lane]["actual_course"] != lane for lane in range(1, 7)
        ),
    }

def _legacy_p1(features: dict[int, dict]) -> dict[int, float]:
    by_course = _by_course(features)
    lane1, lane2, lane3, lane4, lane6 = (by_course[course] for course in (1, 2, 3, 4, 6))
    escape1 = features[lane1]["escape"]
    strong3 = features[lane3]["makuri"] >= 20.0 and features[lane3]["local"] >= 4.5
    strong4 = (
        (escape1 <= 25.0 and features[lane4]["local"] >= 5.3)
        or (features[lane4]["local"] >= 7.2 and features[lane4]["local_st"] <= 0.14)
        or (features[lane4]["p1"] >= 11.5 and features[lane4]["local_st"] <= 0.14)
    )
    strong2 = (
        features[lane2]["p1"] >= 17.0
        and features[lane2]["local_st"] <= 0.17
        and (
            features[lane2]["sashi"] >= 15.0
            or features[lane2]["local"] >= 6.0
            or escape1 <= 10.0
        )
    )
    strong6 = (
        features[lane6]["local"] >= 7.0
        and features[lane6]["motor_trend"] == "up"
        and features[lane6]["straight_rank"] <= 2
    )
    multipliers = {lane: 1.0 for lane in range(1, 7)}
    if strong3:
        multipliers[lane3] = 3.0
    elif strong4:
        multipliers[lane4] = 3.0
    if strong2:
        multipliers[lane2] = 3.0
    if strong6:
        multipliers[lane6] = 3.0
    normalized = _normalize({
        lane: features[lane]["p1"] * multipliers[lane]
        for lane in range(1, 7)
    })
    return {lane: value * 100.0 for lane, value in normalized.items()}

def _live_entries(race: dict, kind: str) -> list[dict]:
    live = race.get("live") or {}
    if kind in live and isinstance(live[kind], dict):
        rows = live[kind].get("entries") or []
        if rows:
            return rows
    if kind == "exhibition":
        return ((live.get("direct") or {}).get("racers") or [])
    if kind == "original":
        for key in ("original", "original_exhibition"):
            rows = ((live.get(key) or {}).get("entries") or [])
            if rows:
                return rows
    return []

def _features(race: dict) -> dict[int, dict]:
    racers = {int(x["lane"]): x for x in race["racers"]}
    direct = ((race.get("live") or {}).get("direct") or {})
    actual_entry = [int(_num(lane)) for lane in direct.get("actual_entry") or []]
    course_by_lane = (
        {lane: course for course, lane in enumerate(actual_entry, 1)}
        if sorted(actual_entry) == list(range(1, 7))
        else {}
    )
    base = race.get("prediction") or race.get("predictionFinal") or {}
    win = base.get("win") or {}
    second = base.get("second") or {}
    third = base.get("third") or {}

    exhibition = {
        int(x["lane"]): x
        for x in _live_entries(race, "exhibition")
        if x.get("lane") is not None
    }
    original = {
        int(x["lane"]): x
        for x in _live_entries(race, "original")
        if x.get("lane") is not None
    }

    f: dict[int, dict] = {}
    for lane, racer in racers.items():
        motor = racer.get("motor_recent") or {}
        ex = exhibition.get(lane) or {}
        ori = original.get(lane) or {}
        f[lane] = {
            "actual_course": course_by_lane.get(
                lane, int(_num(racer.get("actual_course"), lane))
            ),
            "p1": _num(win.get(str(lane))),
            "p2": _num(second.get(str(lane))),
            "p3": _num(third.get(str(lane))),
            "national": _num(racer.get("nat_win")),
            "local": _num(racer.get("local_win")),
            "local_st": _num(racer.get("boaters_local_avg_st") or racer.get("local_st"), 0.18),
            "sashi": _num(racer.get("boaters_sashi_rate")),
            "makuri": _num(racer.get("boaters_makuri_rate")),
            "makuri_sashi": _num(racer.get("boaters_makuri_sashi_rate")),
            "escape": _num(racer.get("boaters_escape_rate")),
            "motor_top2": _num(motor.get("top2_rate")),
            "motor_top3": _num(motor.get("top3_rate")),
            "motor_rate_top2": _num(racer.get("motor_2"), _num(motor.get("top2_rate"))),
            "motor_rate_top3": _num(racer.get("motor_3"), _num(motor.get("top3_rate"))),
            "motor_trend": motor.get("trend"),
            "start_rank": _num(ex.get("start_rank"), 99),
            "exhibition_time": _num(ex.get("exhibition_time"), 99),
            "turn": _num(ori.get("turn_time"), 99),
            "straight": _num(ori.get("straight_time"), 99),
            "lap": _num(ori.get("lap_time"), 99),
            "sum": _num(ori.get("sum"), 99),
            "recent_finishes": _finish_values(racer.get("season_runs") or []),
        }

    for key in ("exhibition_time", "turn", "straight", "lap", "sum"):
        ordered = sorted((f[l][key], l) for l in f if f[l][key] < 90)
        values = [value for value, _ in ordered]
        for lane in f:
            f[lane][f"{key}_rank"] = (
                _rank_low(f[lane][key], values) if f[lane][key] < 90 else 99
            )

    return f

def predict_restored(race: dict, morning_prediction: dict | None = None) -> dict:
    """
    福岡9割版の判断構造を復元するシナリオ層。
    結果・オッズは使用しない。
    現行v1.0のP1/P2/P3を土台に、
    頭シナリオ → 頭別P2 → 頭×2着別P3 を再正規化する。
    """
    f = _features(race)
    if morning_prediction is None:
        morning_win = {lane: f[lane]["p1"] for lane in range(1, 7)}
        current_win = dict(morning_win)
    else:
        morning_features = _features({
            "racers": race["racers"],
            "prediction": morning_prediction,
        })
        morning_win = _legacy_p1(morning_features)
        current_win = _legacy_p1(f)
    p1, p1_audit = build_variable_p1(f, morning_win, current_win=current_win)
    by_course = _by_course(f)
    escape1 = f[by_course[1]]["escape"]

    scored = []
    conditional_audit = []

    def multiply_course(values: dict[int, float], course: int, factor: float) -> None:
        values[by_course[course]] *= factor

    for head, second, third in permutations(range(1, 7), 3):
        second_mult = {lane: 1.0 for lane in range(1, 7)}
        head_course = f[head]["actual_course"]
        second_course = f[second]["actual_course"]

        if head_course == 2:
            multiply_course(second_mult, 1, 1.50)
            multiply_course(second_mult, 4, 1.20)
            multiply_course(second_mult, 5, 1.20)

            two_to_six = (
                f[head]["p1"] >= 17.0
                and 25.0 <= escape1 <= 50.0
                and (
                    f[by_course[6]]["turn_rank"] <= 2
                    or f[by_course[6]]["straight_rank"] <= 2
                )
            )
            if two_to_six:
                multiply_course(second_mult, 6, 2.50)

        elif head_course == 3:
            multiply_course(second_mult, 1, 1.50)
            multiply_course(second_mult, 4, 1.35)
            multiply_course(second_mult, 5, 1.15)

        elif head_course == 4:
            multiply_course(second_mult, 1, 1.55)
            multiply_course(second_mult, 5, 1.35)
            multiply_course(second_mult, 6, 1.18)

            four_to_six = (
                f[head]["p1"] >= 8.0
                and f[by_course[6]]["motor_top3"] >= 70.0
                and f[by_course[6]]["motor_trend"] == "up"
                and f[by_course[6]]["start_rank"] <= 2
                and (
                    f[by_course[6]]["turn_rank"] <= 2
                    or f[by_course[6]]["straight_rank"] <= 2
                )
            )
            if four_to_six:
                multiply_course(second_mult, 6, 2.60)

        elif head_course == 1:
            # 1逃げでも5/6が実戦足・外連動で2着まで上がるケースを残す。
            for course in (5, 6):
                lane = by_course[course]
                if f[lane]["p2"] >= 10.0 and (
                    f[lane]["motor_top3"] >= 60.0 or f[lane]["straight_rank"] <= 2
                ):
                    second_mult[lane] *= 1.70

        p2 = _normalize(
            {lane: f[lane]["p2"] * second_mult[lane] for lane in range(1, 7)},
            {head},
        )

        third_mult = {lane: 1.0 for lane in range(1, 7)}

        if head_course == 2 and second_course == 1:
            multiply_course(third_mult, 3, 1.25)
            multiply_course(third_mult, 4, 1.20)
            multiply_course(third_mult, 5, 1.15)
            multiply_course(third_mult, 6, 1.12)

        if head_course == 2 and second_course == 6:
            multiply_course(third_mult, 3, 1.80)
            multiply_course(third_mult, 1, 1.60)

        if head_course == 3 and second_course == 1:
            multiply_course(third_mult, 4, 1.70)
            multiply_course(third_mult, 5, 1.30)

        if head_course == 4 and second_course == 1:
            multiply_course(third_mult, 5, 1.70)
            multiply_course(third_mult, 6, 1.45)
            multiply_course(third_mult, 3, 1.30)
            multiply_course(third_mult, 2, 1.18)

        if head_course == 4 and second_course == 6:
            multiply_course(third_mult, 1, 2.00)

        if head_course == 1 and second_course in (5, 6):
            multiply_course(third_mult, 3, 1.60)
            multiply_course(third_mult, 4, 1.25)

        p3 = _normalize(
            {lane: f[lane]["p3"] * third_mult[lane] for lane in range(1, 7)},
            {head, second},
        )

        score = p1[head] * p2[second] * p3[third]
        scored.append((score, head, second, third))

    scored.sort(reverse=True)
    top10 = scored[:10]

    tickets = []
    for idx, (score, h, s, t) in enumerate(top10):
        role = "本線" if idx < 6 else ("ずらし" if idx < 8 else "穴")
        tickets.append({
            "combo": f"{h}-{s}-{t}",
            "score": round(score, 8),
            "role": role,
        })

    return {
        "engine": "fukuoka_restore_candidate_v1",
        "result_used": False,
        "odds_used": False,
        "head_scenarios": {
            "strong2": False,
            "strong3": False,
            "strong4": False,
            "strong6": False,
        },
        "p1_audit": p1_audit,
        "entry_changed": p1_audit["entry_changed"],
        "p1": {str(k): round(v * 100, 4) for k, v in p1.items()},
        "tickets": tickets,
        "Main6": [x["combo"] for x in tickets[:6]],
        "Zure2": [x["combo"] for x in tickets[6:8]],
        "Ana2": [x["combo"] for x in tickets[8:10]],
    }
