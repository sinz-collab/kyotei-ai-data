SCENARIOS = {
    "S01_1_ESCAPE": {"name": "1逃げ", "head": [1], "second": [2, 3, 4], "third": [2, 3, 4, 5, 6]},
    "S03_2_SASHI": {"name": "2差し頭", "head": [2], "second": [1, 3, 4], "third": [1, 3, 4, 5, 6]},
    "S05_3_MAKURI": {"name": "3まくり", "head": [3], "second": [4, 5, 1, 6], "third": [1, 2, 4, 5, 6]},
    "S06_3_MAKURIZASHI": {"name": "3まくり差し", "head": [3], "second": [1, 4, 2], "third": [1, 2, 4, 5, 6]},
    "S07_3_ATTACK_LINK": {"name": "3攻めから4・5・6連動", "head": [3], "second": [4, 5, 6, 1], "third": [4, 5, 6, 1, 2]},
    "S08_4_KADO": {"name": "4カド攻め", "head": [4], "second": [5, 6, 1, 2, 3], "third": [1, 2, 3, 5, 6]},
    "S09_4_ATTACK_LINK": {"name": "4攻めから5・6連動", "head": [4], "second": [5, 6, 1], "third": [5, 6, 1, 2, 3]},
    "S10_1_3_BATTLE": {"name": "1と3の競りから4・5差し", "head": [4, 5], "second": [1, 3, 4, 5], "third": [1, 2, 3, 4, 5, 6]},
    "S11_WALL_FAILURE": {"name": "壁役不成立", "head": [2, 3, 4, 5], "second": [1, 3, 4, 5, 6], "third": [1, 2, 3, 4, 5, 6]},
    "S12_INSIDE_COLLAPSE": {"name": "内崩れ", "head": [3, 4, 5, 6], "second": [3, 4, 5, 6, 1], "third": [1, 2, 3, 4, 5, 6]},
    "S13_OUTER_THIRD": {"name": "外枠3着連動", "head": [1, 2, 3, 4], "second": [1, 2, 3, 4, 5], "third": [5, 6]},
    "S14_ENTRY_CHANGE": {"name": "進入変更による展開変化", "head": [1, 2, 3, 4, 5, 6], "second": [1, 2, 3, 4, 5, 6], "third": [1, 2, 3, 4, 5, 6]},
    "S15_4_ATTACK_5_HEAD": {"name": "4攻めから5まくり差し頭", "head": [5], "second": [4, 3, 1, 6], "third": [3, 4, 1, 6, 2]},
}


def _clip(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def evaluate_scenarios(boats, water):
    by_course = {int(boat["actual_course"]): boat for boat in boats}

    def probability(course, key="win_prob"):
        return float(by_course.get(course, {}).get(key, 0.0))

    scores = {key: 0.025 for key in SCENARIOS}

    wall = (probability(2) + probability(3)) * 0.5
    attack3 = probability(3) + max(0.0, water.get("center_bias", 0.0)) * 0.8
    attack4 = probability(4) + max(0.0, water.get("center_bias", 0.0)) * 0.7

    escape = (
        probability(1)
        + water.get("escape_bias", 0.0)
        + max(0.0, wall - 0.15) * 0.18
        - max(0.0, attack3 - 0.17) * 0.55
        - max(0.0, attack4 - 0.14) * 0.32
    )
    scores["S01_1_ESCAPE"] += _clip(escape - 0.28, 0.0, 0.25)
    scores["S03_2_SASHI"] += _clip(probability(2) - 0.10, 0.0, 0.22)
    scores["S05_3_MAKURI"] += _clip(probability(3) - 0.105, 0.0, 0.24) + max(0.0, water.get("center_bias", 0.0)) * 0.55
    scores["S06_3_MAKURIZASHI"] += _clip(probability(3) - 0.12, 0.0, 0.20) + max(0.0, probability(1) - 0.34) * 0.12
    scores["S07_3_ATTACK_LINK"] += _clip(probability(3) - 0.11, 0.0, 0.20) + max(0.0, water.get("center_bias", 0.0)) * 0.70
    scores["S08_4_KADO"] += _clip(probability(4) - 0.09, 0.0, 0.23) + max(0.0, water.get("center_bias", 0.0)) * 0.50
    scores["S09_4_ATTACK_LINK"] += _clip(probability(4) - 0.10, 0.0, 0.20) + max(0.0, water.get("outer_bias", 0.0)) * 0.45

    if attack3 > 0.18 and probability(1) > 0.30:
        scores["S10_1_3_BATTLE"] += 0.10
    if probability(2) < 0.11:
        scores["S11_WALL_FAILURE"] += 0.10
    if probability(1) < 0.34 and water.get("center_bias", 0.0) > 0:
        scores["S12_INSIDE_COLLAPSE"] += 0.11
    if water.get("outer_bias", 0.0) > 0:
        scores["S13_OUTER_THIRD"] += min(0.18, water["outer_bias"] * 1.5)
    if any(boat.get("entry_changed") for boat in boats):
        scores["S14_ENTRY_CHANGE"] += 0.30

    five_head_bonus = float(water.get("five_head_scenario_bonus", 0.0) or 0.0)
    scores["S15_4_ATTACK_5_HEAD"] += min(0.18, max(0.0, five_head_bonus))

    total = sum(scores.values()) or 1.0
    output = []
    for scenario_id, value in scores.items():
        item = dict(SCENARIOS[scenario_id])
        item.update({"id": scenario_id, "probability": value / total})
        output.append(item)
    return sorted(output, key=lambda item: item["probability"], reverse=True)


def scenario_position_adjustments(scenarios):
    adjustments = {
        lane: {"win": 0.0, "second": 0.0, "third": 0.0}
        for lane in range(1, 7)
    }
    for scenario in scenarios[:6]:
        weight = float(scenario["probability"])
        for lane in scenario.get("head", []):
            adjustments[lane]["win"] += weight
        for lane in scenario.get("second", []):
            adjustments[lane]["second"] += weight / max(1, len(scenario["second"]))
        for lane in scenario.get("third", []):
            adjustments[lane]["third"] += weight / max(1, len(scenario["third"]))
    return adjustments
