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

    # 主シナリオ連動枠。
    # 最上位シナリオが18%以上で、単独頭が明確な場合は、その展開の上位2点を必ず残す。
    # 結果固定ではなく、事前に生成されたシナリオ確率だけを使用する。
    primary = scenarios[0] if scenarios else {}
    primary_probability = float(primary.get("probability") or 0.0)
    primary_heads = [int(x) for x in (primary.get("head") or [])]
    primary_seconds = [int(x) for x in (primary.get("second") or [])]
    primary_thirds = [int(x) for x in (primary.get("third") or [])]

    if primary_probability >= 0.18 and len(primary_heads) == 1:
        scenario_head = primary_heads[0]
        scenario_candidates = [
            row for row in candidates
            if row["first"] == scenario_head
            and row["second"] in primary_seconds
            and row["third"] in primary_thirds
        ]
        scenario_candidates.sort(
            key=lambda row: (
                primary_seconds.index(row["second"]) if row["second"] in primary_seconds else 99,
                primary_thirds.index(row["third"]) if row["third"] in primary_thirds else 99,
                -row["score"],
            )
        )
        for row in scenario_candidates[:2]:
            item = dict(row)
            item["type"] = "deviation" if scenario_head == axis_lane else "upset"
            item["scenario_reserved"] = True
            item["scenario_id"] = primary.get("id")
            selected.append(item)
            selected_combinations.add(item["combination"])

    # 本線6点。シナリオ予約枠を含め総数を10点以内に維持する。
    for row in candidates:
        if sum(item["type"] == "main" for item in selected) >= 6:
            break
        if row["combination"] in selected_combinations:
            continue
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
