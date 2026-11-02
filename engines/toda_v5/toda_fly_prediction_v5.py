from __future__ import annotations

import math

from toda_ticket_engine_v5 import conditional_third
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
    raw_value = (racer or {}).get(key)
    if raw_value is None or raw_value == "":
        return 0.0
    value = _optional_number(raw_value)
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


def _probability_maps(prediction, baseline=False):
    source = prediction.get("_baseline") if baseline else prediction
    source = source if isinstance(source, dict) else prediction
    return source["win"], source["secondByHead"], source["thirdByHead"]


def _scenario_map(prediction, baseline=False):
    source = prediction.get("_baseline") if baseline else prediction
    source = source if isinstance(source, dict) else prediction
    return {
        int(row.get("head") or 0): row
        for row in source.get("scenarios") or []
        if int(row.get("head") or 0) in (2, 3, 4, 5, 6)
    }


def _combo_values(combo, maps):
    head, second, third = map(int, combo.split("-"))
    win, second_by_head, third_by_head = maps
    second_probability = num(second_by_head[str(head)][str(second)])
    third_probability = num(conditional_third(head, second, third_by_head)[str(third)])
    head_conditional = second_probability * third_probability / 100.0
    return num(win[str(head)]) * head_conditional / 100.0, head_conditional


def _scenario_establishment(row):
    row = row or {}
    return clamp(
        num(row.get("attackEstablishment"), num(row.get("weight"), 0.0)),
        0.0,
        1.0,
    )


def build_fly_tickets(prediction, status="final"):
    live_blend = status == "final" and isinstance(prediction.get("_baseline"), dict)
    morning_weight = 0.8 if live_blend else 1.0
    live_weight = 0.2 if live_blend else 0.0
    morning_maps = _probability_maps(prediction, baseline=live_blend)
    live_maps = _probability_maps(prediction)
    morning_scenarios = _scenario_map(prediction, baseline=live_blend)
    live_scenarios = _scenario_map(prediction)
    scenarios = prediction.get("scenarios") or []
    candidates = []

    for head in (2, 3, 4, 5, 6):
        morning_establishment = _scenario_establishment(morning_scenarios.get(head))
        live_establishment = _scenario_establishment(live_scenarios.get(head))
        establishment = morning_weight * morning_establishment + live_weight * live_establishment
        for second in LANES:
            if second == head:
                continue
            for third in LANES:
                if third in (head, second):
                    continue
                combo = f"{head}-{second}-{third}"
                morning_probability, morning_conditional = _combo_values(combo, morning_maps)
                live_probability, live_conditional = _combo_values(combo, live_maps)
                probability = morning_weight * morning_probability + live_weight * live_probability
                head_conditional = morning_weight * morning_conditional + live_weight * live_conditional
                scenario_score = (
                    morning_weight * morning_establishment * morning_conditional
                    + live_weight * live_establishment * live_conditional
                )
                candidates.append({
                    "combo": combo,
                    "head": head,
                    "probability": probability,
                    "headConditionalProbability": head_conditional,
                    "scenarioEstablishment": establishment,
                    "scenarioScore": scenario_score,
                    "scenarioIds": _scenario_ids(scenarios, head),
                })

    probability_ranked = sorted(
        candidates,
        key=lambda row: (-row["probability"], row["combo"]),
    )
    selected = []
    seen = set()
    for row in probability_ranked[:8]:
        seen.add(row["combo"])
        selected.append({
            "combo": row["combo"],
            "role": "条件付き確率上位",
            "head": row["head"],
            "pattern": "conditional_top8",
            "prob": round(row["probability"], 4),
            "headConditionalProbability": round(row["headConditionalProbability"], 4),
            "scenarioIds": row["scenarioIds"],
        })

    scenario_ranked = sorted(
        (row for row in candidates if row["combo"] not in seen),
        key=lambda row: (-row["scenarioScore"], -row["headConditionalProbability"], row["combo"]),
    )
    for row in scenario_ranked[:2]:
        seen.add(row["combo"])
        selected.append({
            "combo": row["combo"],
            "role": "展開シナリオ",
            "head": row["head"],
            "pattern": "scenario_top2",
            "prob": round(row["probability"], 4),
            "headConditionalProbability": round(row["headConditionalProbability"], 4),
            "scenarioEstablishment": round(row["scenarioEstablishment"], 6),
            "scenarioScore": round(row["scenarioScore"], 6),
            "scenarioIds": row["scenarioIds"],
        })

    heads = _fly_heads(prediction)
    main_head, second_head = heads[:2]
    development_head = selected[8]["head"] if len(selected) > 8 else heads[2]

    if any(int(row["combo"].split("-")[0]) == 1 for row in selected):
        raise RuntimeError("toda_fly_ticket_lane1_head_forbidden")
    shortage = max(0, 10 - len(selected))
    return {
        "mainHead": main_head,
        "secondHead": second_head,
        "developmentHead": development_head,
        "tickets": selected,
        "ticketStatus": "complete" if shortage == 0 else "insufficient",
        "ticketShortage": shortage,
        "ticketShortageReason": None if shortage == 0 else "valid_unique_candidates_below_10",
        "ticketMethod": "conditional_top8_plus_scenario_top2",
        "morningWeight": morning_weight,
        "liveWeight": live_weight,
    }


def build_fly_prediction(prediction, racers, status="final"):
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
    ticket_result = build_fly_tickets(prediction, status=status)
    return {
        "probability": round(probability, 1),
        "threshold": FLY_THRESHOLD,
        "isFly": is_fly,
        "judgement": "飛び優勢" if is_fly else "逃げ優勢",
        "status": status,
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
