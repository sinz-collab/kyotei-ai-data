from __future__ import annotations

import math

from toda_ticket_engine_v5 import combo_prob
from toda_utils_v5 import LANES, clamp, num


TODA_LANE1_ESCAPE_BASELINE = 44.813005666705216
EMPIRICAL_BAYES_PRIOR_N = 20.0
BASE_FLY_WEIGHT = 0.65
CURRENT_P1_WEIGHT = 0.35
RAW_FLY_MIN = 5.0
RAW_FLY_MAX = 95.0
SIGMOID_INTERCEPT = -4.089776194435157
SIGMOID_SLOPE = 0.06374919859354038
FLY_THRESHOLD = 50.0


def _optional_number(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _lane(racers, lane):
    return next((row for row in racers or [] if int(row.get("lane") or 0) == lane), None)


def _base_fly(racers):
    lane1 = _lane(racers, 1) or {}
    personal_escape = _optional_number(lane1.get("boaters_escape_rate"))
    sample_n = _optional_number(lane1.get("boaters_kimarite_starts"))
    fallback = personal_escape is None or sample_n is None or sample_n < 0
    if fallback:
        adjusted_escape = TODA_LANE1_ESCAPE_BASELINE
    else:
        weight = sample_n / (sample_n + EMPIRICAL_BAYES_PRIOR_N)
        adjusted_escape = weight * personal_escape + (1.0 - weight) * TODA_LANE1_ESCAPE_BASELINE
    return {
        "personalEscape": personal_escape,
        "sampleN": sample_n,
        "adjustedEscape": adjusted_escape,
        "baseFly": 100.0 - adjusted_escape,
        "fallbackApplied": fallback,
    }


def _percentage(racer, key):
    value = _optional_number((racer or {}).get(key))
    if value is None:
        raise RuntimeError(f"toda_fly_attack_rate_missing: {key}")
    return clamp(value, 0.0, 100.0)


def _attack_matchup(racers):
    lane1 = _lane(racers, 1) or {}
    lane2 = _lane(racers, 2) or {}
    lane3 = _lane(racers, 3) or {}
    lane4 = _lane(racers, 4) or {}
    pairs = [
        ("1_sashare_x_2_sashi", _percentage(lane1, "boaters_sashare_rate"), _percentage(lane2, "boaters_sashi_rate")),
        ("1_makurare_x_3_makuri", _percentage(lane1, "boaters_makurare_rate"), _percentage(lane3, "boaters_makuri_rate")),
        ("1_makurare_zashi_x_3_makuri_sashi", _percentage(lane1, "boaters_makurare_zashi_rate"), _percentage(lane3, "boaters_makuri_sashi_rate")),
        ("1_makurare_x_4_makuri", _percentage(lane1, "boaters_makurare_rate"), _percentage(lane4, "boaters_makuri_rate")),
        ("1_makurare_zashi_x_4_makuri_sashi", _percentage(lane1, "boaters_makurare_zashi_rate"), _percentage(lane4, "boaters_makuri_sashi_rate")),
    ]
    components = [
        {"id": key, "insideRate": inside, "attackRate": attack, "matchup": inside * attack / 100.0}
        for key, inside, attack in pairs
    ]
    return sum(row["matchup"] for row in components), components


def _inside_resistance(scenarios):
    row = next(
        (scenario for scenario in scenarios or [] if scenario.get("id") == "CURRENT_FORM_INSIDE_RESISTANCE"),
        None,
    )
    if row is None:
        return 0.0, 0.0, None
    strength = clamp(num((row.get("insideResistance") or {}).get("strength"), 0.0), 0.0, 1.0)
    return min(12.0, 8.0 + 8.0 * strength), strength, row.get("id")


def _scenario_ids(scenarios, head):
    return [
        str(row["id"])
        for row in scenarios or []
        if int(row.get("head") or 0) == head and row.get("id")
    ]


def _ranked_combos(prediction, head, category=None):
    rows = []
    for second in LANES:
        if second == head:
            continue
        for third in LANES:
            if third in (head, second):
                continue
            if category == "lane1Second" and second != 1:
                continue
            if category == "lane1Third" and third != 1:
                continue
            if category == "lane1Out" and 1 in (second, third):
                continue
            combo = f"{head}-{second}-{third}"
            rows.append((
                combo_prob(combo, prediction["win"], prediction["secondByHead"], prediction["thirdByHead"]),
                combo,
            ))
    return sorted(rows, key=lambda row: (-row[0], row[1]))


def _fly_heads(prediction):
    scenario_heads = {
        int(row.get("head") or 0)
        for row in prediction.get("scenarios") or []
        if int(row.get("head") or 0) in (2, 3, 4, 5, 6)
        and num(row.get("attackEstablishment"), row.get("weight")) > 0
    }
    ranked = sorted(
        scenario_heads,
        key=lambda head: (-num(prediction["win"].get(str(head))), head),
    )
    ranked.extend(
        sorted(
            (head for head in (2, 3, 4, 5, 6) if head not in scenario_heads),
            key=lambda head: (-num(prediction["win"].get(str(head))), head),
        )
    )
    return ranked


def build_fly_tickets(prediction):
    heads = _fly_heads(prediction)
    main_head, second_head, development_head = heads[:3]
    scenarios = prediction.get("scenarios") or []
    selected = []
    seen = set()

    def add(head, category, role, limit):
        added = 0
        for probability, combo in _ranked_combos(prediction, head, category):
            if combo in seen:
                continue
            seen.add(combo)
            selected.append({
                "combo": combo,
                "role": role,
                "head": head,
                "pattern": category,
                "prob": round(probability, 4),
                "scenarioIds": _scenario_ids(scenarios, head),
            })
            added += 1
            if added >= limit:
                break

    add(main_head, "lane1Second", "Main HEAD", 2)
    add(main_head, "lane1Third", "Main HEAD", 1)
    add(main_head, "lane1Out", "Main HEAD", 2)
    add(second_head, "lane1Second", "Second HEAD", 2)
    add(second_head, "lane1Third", "Second HEAD", 1)
    add(development_head, None, "展開連動", 2)

    if len(selected) != 10 or len(seen) != 10:
        raise RuntimeError(f"toda_fly_tickets_must_be_10_unique: {len(selected)}")
    if any(int(row["combo"].split("-")[0]) == 1 for row in selected):
        raise RuntimeError("toda_fly_ticket_lane1_head_forbidden")
    return {
        "mainHead": main_head,
        "secondHead": second_head,
        "developmentHead": development_head,
        "tickets": selected,
    }


def build_fly_prediction(prediction, racers):
    base = _base_fly(racers)
    current_p1 = num((prediction.get("win") or {}).get("1"), -1.0)
    if not 0.0 <= current_p1 <= 100.0:
        raise RuntimeError("toda_final_current_p1_missing")
    matchup, matchup_components = _attack_matchup(racers)
    attack_adj = min(12.0, matchup * 1.5)
    resistance_adj, resistance_strength, resistance_scenario_id = _inside_resistance(
        prediction.get("scenarios")
    )
    raw_fly_score = clamp(
        BASE_FLY_WEIGHT * base["baseFly"]
        + CURRENT_P1_WEIGHT * (100.0 - current_p1)
        + attack_adj
        + resistance_adj,
        RAW_FLY_MIN,
        RAW_FLY_MAX,
    )
    probability = 100.0 / (
        1.0 + math.exp(-(SIGMOID_INTERCEPT + SIGMOID_SLOPE * raw_fly_score))
    )
    is_fly = probability >= FLY_THRESHOLD
    ticket_result = build_fly_tickets(prediction) if is_fly else {
        "mainHead": None,
        "secondHead": None,
        "developmentHead": None,
        "tickets": [],
    }
    return {
        "probability": round(probability, 1),
        "threshold": FLY_THRESHOLD,
        "isFly": is_fly,
        "status": "final",
        "rawFlyScore": round(raw_fly_score, 6),
        "baseFly": round(base["baseFly"], 6),
        "currentP1": round(current_p1, 6),
        "attackAdj": round(attack_adj, 6),
        "resistanceAdj": round(resistance_adj, 6),
        "currentP1Weight": CURRENT_P1_WEIGHT,
        "baseFlyWeight": BASE_FLY_WEIGHT,
        "personalEscape": base["personalEscape"],
        "sampleN": base["sampleN"],
        "adjustedEscape": round(base["adjustedEscape"], 6),
        "personalEscapeFallback": base["fallbackApplied"],
        "attackMatchup": round(matchup, 6),
        "attackMatchupComponents": matchup_components,
        "insideResistanceStrength": round(resistance_strength, 6),
        "resistanceScenarioId": resistance_scenario_id,
        "sigmoidIntercept": SIGMOID_INTERCEPT,
        "sigmoidSlope": SIGMOID_SLOPE,
        "oddsUsed": False,
        **ticket_result,
    }
