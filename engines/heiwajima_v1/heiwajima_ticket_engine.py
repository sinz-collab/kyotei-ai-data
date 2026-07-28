from itertools import permutations


def generate_tickets(boats, scenarios, max_tickets=10):
    probs = {int(b["boat_no"]): b for b in boats}
    scenario_map = {}

    for scenario in scenarios[:6]:
        probability = float(scenario.get("probability") or 0.0)

        for head in scenario.get("head", []):
            head = int(head)
            scenario_map.setdefault(head, {"second": {}, "third": {}})

            for lane in scenario.get("second", []):
                lane = int(lane)
                scenario_map[head]["second"][lane] = (
                    scenario_map[head]["second"].get(lane, 0.0)
                    + probability
                )

            for lane in scenario.get("third", []):
                lane = int(lane)
                scenario_map[head]["third"][lane] = (
                    scenario_map[head]["third"].get(lane, 0.0)
                    + probability
                )

    candidates = []

    for first, second, third in permutations(range(1, 7), 3):
        base_score = (
            probs[first]["win_prob"]
            * probs[second]["second_prob"]
            * probs[third]["third_prob"]
        )

        link = 1.0

        if first in scenario_map:
            second_link = scenario_map[first]["second"].get(second, 0.0)
            third_link = scenario_map[first]["third"].get(third, 0.0)

            link += min(
                0.40,
                second_link * 0.75
                + third_link * 0.40,
            )

        candidates.append(
            {
                "combination": f"{first}-{second}-{third}",
                "first": first,
                "second": second,
                "third": third,
                "score": base_score * link,
            }
        )

    candidates.sort(key=lambda row: row["score"], reverse=True)

    if not candidates:
        return []

    axis_lane = candidates[0]["first"]
    selected = []

    # 本線6点
    for row in candidates:
        if len(selected) >= 6:
            break

        item = dict(row)
        item["type"] = "main"
        selected.append(item)

    selected_combos = {row["combination"] for row in selected}

    # ズレ対応2点
    for row in candidates:
        if len([x for x in selected if x["type"] == "deviation"]) >= 2:
            break

        if row["combination"] in selected_combos:
            continue

        item = dict(row)
        item["type"] = "deviation"
        selected.append(item)
        selected_combos.add(row["combination"])

    # 荒れ対応2点。本命頭と異なる頭を優先
    upset_candidates = [
        row for row in candidates
        if row["combination"] not in selected_combos
        and row["first"] != axis_lane
    ]

    for row in upset_candidates[:2]:
        item = dict(row)
        item["type"] = "upset"
        selected.append(item)
        selected_combos.add(row["combination"])

    # 外頭候補が不足した場合も重複なしで10点まで補完
    for row in candidates:
        if len(selected) >= max_tickets:
            break

        if row["combination"] in selected_combos:
            continue

        item = dict(row)
        item["type"] = "upset"
        selected.append(item)
        selected_combos.add(row["combination"])

    selected = selected[:max_tickets]

    total = sum(row["score"] for row in selected) or 1.0

    for row in selected:
        row["share"] = round(row["score"] / total, 4)

    return selected
