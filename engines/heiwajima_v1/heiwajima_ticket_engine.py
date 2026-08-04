from itertools import permutations


def generate_tickets(boats, scenarios, max_tickets=10):
    probabilities = {int(boat["boat_no"]): boat for boat in boats}
    scenario_map = {}

    for scenario in scenarios[:6]:
        probability = float(scenario.get("probability") or 0.0)
        for head in scenario.get("head", []):
            head = int(head)
            scenario_map.setdefault(head, {"second": {}, "third": {}})
            for lane in scenario.get("second", []):
                lane = int(lane)
                scenario_map[head]["second"][lane] = (
                    scenario_map[head]["second"].get(lane, 0.0) + probability
                )
            for lane in scenario.get("third", []):
                lane = int(lane)
                scenario_map[head]["third"][lane] = (
                    scenario_map[head]["third"].get(lane, 0.0) + probability
                )

    candidates = []
    for first, second, third in permutations(range(1, 7), 3):
        base_score = (
            probabilities[first]["win_prob"]
            * probabilities[second]["second_prob"]
            * probabilities[third]["third_prob"]
        )

        link = 1.0
        if first == 5:
            if second == 4:
                link += 0.18
            elif second == 3:
                link += 0.10
            if third in (3, 4, 1, 6):
                link += 0.08

        if first in scenario_map:
            second_link = scenario_map[first]["second"].get(second, 0.0)
            third_link = scenario_map[first]["third"].get(third, 0.0)
            link += min(0.40, second_link * 0.75 + third_link * 0.40)

        candidates.append({
            "combination": f"{first}-{second}-{third}",
            "first": first,
            "second": second,
            "third": third,
            "score": base_score * link,
        })

    candidates.sort(key=lambda row: row["score"], reverse=True)
    if not candidates:
        return []

    axis_lane = candidates[0]["first"]
    selected = []
    selected_combinations = set()

    # 本線6点
    for row in candidates:
        if len(selected) >= 6:
            break
        item = dict(row)
        item["type"] = "main"
        selected.append(item)
        selected_combinations.add(item["combination"])

    # ズレ対応2点
    for row in candidates:
        if sum(item["type"] == "deviation" for item in selected) >= 2:
            break
        if row["combination"] in selected_combinations:
            continue
        item = dict(row)
        item["type"] = "deviation"
        selected.append(item)
        selected_combinations.add(item["combination"])

    # 荒れ対応2点。本命頭以外を優先
    for row in candidates:
        if sum(item["type"] == "upset" for item in selected) >= 2:
            break
        if row["combination"] in selected_combinations or row["first"] == axis_lane:
            continue
        item = dict(row)
        item["type"] = "upset"
        selected.append(item)
        selected_combinations.add(item["combination"])

    # 不足時の補完
    for row in candidates:
        if len(selected) >= max_tickets:
            break
        if row["combination"] in selected_combinations:
            continue
        item = dict(row)
        item["type"] = "upset"
        selected.append(item)
        selected_combinations.add(item["combination"])

    selected = selected[:max_tickets]
    total = sum(row["score"] for row in selected) or 1.0
    for row in selected:
        row["share"] = round(row["score"] / total, 4)
    return selected
