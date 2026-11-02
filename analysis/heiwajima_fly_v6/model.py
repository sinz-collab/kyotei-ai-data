from __future__ import annotations

import math
from itertools import permutations

from heiwajima_v2_7_adjustments import apply_conditional_placement


MODEL_ID = "heiwajima_fly_v6_candidate_20261102"
THRESHOLD = 0.50
FEATURE_ORDER = (
    "lane1_escape_weakness",
    "lane1_loss_pattern",
    "best_outer_attack",
    "player_course_matchup",
    "exhibition_sum_matchup",
    "lap_matchup",
)
# Frozen candidate coefficients. Inputs are clipped to [-2, 2]. There is no
# tide, wind, result, payout, odds, or stored-prediction term.
INTERCEPT = -0.34
COEFFICIENTS = {
    "lane1_escape_weakness": 0.42,
    "lane1_loss_pattern": 0.24,
    "best_outer_attack": 0.54,
    "player_course_matchup": 0.32,
    "exhibition_sum_matchup": 0.18,
    "lap_matchup": 0.18,
}
FLY_TICKET_WEIGHTS = {
    "head_balance": 0.50,
    "conditional_placement": 0.25,
    "attack_scenario": 0.25,
}


def _number(value, default=None):
    if value in (None, "", "-"):
        return default
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _rate(value):
    value = _number(value)
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1.0 else value


def _clip(value, low=-2.0, high=2.0):
    return max(low, min(high, value))


def _normalise(values):
    total = sum(max(0.0, float(value)) for value in values.values())
    if total <= 0.0:
        equal = 1.0 / len(values)
        return {key: equal for key in values}
    return {key: max(0.0, float(value)) / total for key, value in values.items()}


def _kimarite(boat, key):
    return _rate((boat.get("kimarite") or {}).get(key))


def extract_features(input_data, loader):
    boats = {int(boat["boat_no"]): boat for boat in input_data["boats"]}
    missing = []
    lane1 = boats[1]

    escape = _kimarite(lane1, "escape_rate")
    if escape is None:
        missing.append("lane1_escape_rate")
    escape_weakness = 0.0 if escape is None else (0.50 - escape) / 0.25

    loss_rates = [
        _kimarite(lane1, "sashare_rate"),
        _kimarite(lane1, "makurare_rate"),
        _kimarite(lane1, "makurare_zashi_rate"),
    ]
    available_loss = [value for value in loss_rates if value is not None]
    if not available_loss:
        missing.append("lane1_loss_pattern")
    loss_pattern = 0.0 if not available_loss else (sum(available_loss) - 0.50) / 0.25

    attacks = []
    for lane in range(2, 7):
        rates = [
            _kimarite(boats[lane], "sashi_rate"),
            _kimarite(boats[lane], "makuri_rate"),
            _kimarite(boats[lane], "makuri_sashi_rate"),
        ]
        present = [value for value in rates if value is not None]
        if present:
            attacks.append(sum(present))
    if not attacks:
        missing.append("outer_attack_rates")
    best_outer_attack = 0.0 if not attacks else (max(attacks) - 0.25) / 0.25

    course_rates = {}
    for lane, boat in boats.items():
        frame = loader.player_course(boat.get("reg_no"), boat.get("actual_course") or lane)
        if not frame.empty:
            course_rates[lane] = _rate(frame.iloc[0].get("win_rate"))
    outer_course = [value for lane, value in course_rates.items() if lane != 1 and value is not None]
    if course_rates.get(1) is None or not outer_course:
        missing.append("player_course_win_rate")
        player_course_matchup = 0.0
    else:
        player_course_matchup = (max(outer_course) - course_rates[1]) / 0.25

    exhibitions = {
        int(row["boat_no"]): row
        for row in ((input_data.get("live") or {}).get("exhibitions") or [])
        if row.get("boat_no")
    }

    def live_matchup(key):
        raw_key = {"sum_score": "sum_difference", "lap_score": "lap_time"}[key]
        lane1_row = exhibitions.get(1) or {}
        lane1_value = _number(lane1_row.get(key)) if _number(lane1_row.get(raw_key)) is not None else None
        outer = [
            _number((exhibitions.get(lane) or {}).get(key))
            if _number((exhibitions.get(lane) or {}).get(raw_key)) is not None
            else None
            for lane in range(2, 7)
        ]
        outer = [value for value in outer if value is not None]
        if lane1_value is None or not outer:
            missing.append(key)
            return 0.0
        return max(outer) - lane1_value

    raw = {
        "lane1_escape_weakness": escape_weakness,
        "lane1_loss_pattern": loss_pattern,
        "best_outer_attack": best_outer_attack,
        "player_course_matchup": player_course_matchup,
        "exhibition_sum_matchup": live_matchup("sum_score"),
        "lap_matchup": live_matchup("lap_score"),
    }
    return {key: round(_clip(raw[key]), 8) for key in FEATURE_ORDER}, sorted(set(missing))


def fly_probability(input_data, loader):
    features, missing = extract_features(input_data, loader)
    logit = INTERCEPT + sum(COEFFICIENTS[key] * features[key] for key in FEATURE_ORDER)
    probability = 1.0 / (1.0 + math.exp(-logit))
    return {
        "model_id": MODEL_ID,
        "probability": round(probability, 8),
        "is_fly": probability >= THRESHOLD,
        "threshold": THRESHOLD,
        "features": features,
        "missing": missing,
    }


def normal_tickets(records, limit=12):
    by_lane = {int(row["boat_no"]): row for row in records}
    rows = []
    for first, second, third in permutations(range(1, 7), 3):
        score = (
            float(by_lane[first]["win_prob"])
            * float(by_lane[second]["second_prob"])
            * float(by_lane[third]["third_prob"])
        ) ** 0.97
        rows.append((score, f"{first}-{second}-{third}"))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [combo for _, combo in rows[:limit]]


def _scenario_components(scenarios):
    head_values = {lane: 0.0 for lane in range(2, 7)}
    pair_values = {
        head: {(second, third): 0.0 for second, third in permutations(range(1, 7), 2) if head not in (second, third)}
        for head in range(2, 7)
    }
    for scenario in scenarios:
        probability = float(scenario.get("probability") or 0.0)
        heads = [int(value) for value in scenario.get("head") or [] if int(value) != 1]
        for head in heads:
            if head not in pair_values:
                continue
            head_values[head] += probability
            seconds = [int(value) for value in scenario.get("second") or [] if int(value) != head]
            thirds = [int(value) for value in scenario.get("third") or [] if int(value) != head]
            for second_index, second in enumerate(seconds):
                for third_index, third in enumerate(thirds):
                    if second != third and (second, third) in pair_values[head]:
                        pair_values[head][(second, third)] += probability / ((second_index + 1) * (third_index + 1))
    return _normalise(head_values), {head: _normalise(values) for head, values in pair_values.items()}


def fly_tickets(records, scenarios, history_rows, limit=8):
    by_lane = {int(row["boat_no"]): row for row in records}
    conditional = apply_conditional_placement([dict(row) for row in records], scenarios, history_rows)["pairs"]
    head_balance = _normalise({lane: math.sqrt(max(0.0, float(by_lane[lane]["win_prob"]))) for lane in range(2, 7)})
    scenario_heads, scenario_pairs = _scenario_components(scenarios)

    keys = [(head, second, third) for head in range(2, 7) for second, third in permutations(range(1, 7), 2) if head not in (second, third)]
    head_component = _normalise({key: head_balance[key[0]] * float(by_lane[key[1]]["second_prob"]) * float(by_lane[key[2]]["third_prob"]) for key in keys})
    conditional_component = _normalise({key: head_balance[key[0]] * conditional[key[0]][(key[1], key[2])] for key in keys})
    scenario_component = _normalise({key: scenario_heads[key[0]] * scenario_pairs[key[0]][(key[1], key[2])] for key in keys})
    ranked = []
    for key in keys:
        score = (
            FLY_TICKET_WEIGHTS["head_balance"] * head_component[key]
            + FLY_TICKET_WEIGHTS["conditional_placement"] * conditional_component[key]
            + FLY_TICKET_WEIGHTS["attack_scenario"] * scenario_component[key]
        )
        combo = f"{key[0]}-{key[1]}-{key[2]}"
        ranked.append((score, combo))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [combo for _, combo in ranked[:limit]]

