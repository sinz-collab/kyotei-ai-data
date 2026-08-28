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
                scenario_map[head]["second"][lane] = scenario_map[head]["second"].get(lane, 0.0) + probability
            for lane in scenario.get("third", []):
                lane = int(lane)
                scenario_map[head]["third"][lane] = scenario_map[head]["third"].get(lane, 0.0) + probability

    candidates = []
    for first, second, third in permutations(range(1,7),3):
        base_score = probabilities[first]["win_prob"] * probabilities[second]["second_prob"] * probabilities[third]["third_prob"]
        link = 1.0
        if first in scenario_map:
            second_link = scenario_map[first]["second"].get(second, 0.0)
            third_link = scenario_map[first]["third"].get(third, 0.0)
            link += min(0.40, second_link*0.75 + third_link*0.40)
        if first in (5,6) and probabilities[first]["win_prob"] >= 0.09:
            if second in (2,3,4,5,6) and second != first: link += 0.05
            if third in (4,5,6) and third != first: link += 0.05
        candidates.append({
            "combination":f"{first}-{second}-{third}",
            "first":first,"second":second,"third":third,"score":base_score*link,
        })
    candidates.sort(key=lambda row:row["score"], reverse=True)
    if not candidates: return []

    head_rank = sorted(probabilities, key=lambda lane:probabilities[lane]["win_prob"], reverse=True)
    axis_lane = head_rank[0]
    axis_prob = probabilities[axis_lane]["win_prob"]
    selected, used = [], set()

    def reserve(head, role):
        for row in candidates:
            if row["first"] == head and row["combination"] not in used:
                item = dict(row); item["type"] = role; item["head_reserved"] = True
                selected.append(item); used.add(item["combination"]); return True
        return False

    # Reserve every non-axis head whose probability is both independently
    # meaningful and close enough to the axis.  The strongest is classified
    # as upset; additional near-equal heads use deviation so an eligible
    # outer-course head can still occupy the second upset slot below.
    comparable_heads = []
    for head in head_rank[1:]:
        p = probabilities[head]["win_prob"]
        if p >= 0.14 and p >= axis_prob*0.35:
            comparable_heads.append(head)
    for index, head in enumerate(comparable_heads):
        reserve(head, "upset" if index == 0 else "deviation")

    outer = max((5,6), key=lambda lane:probabilities[lane]["win_prob"])
    if probabilities[outer]["win_prob"] >= 0.09 and outer not in {x["first"] for x in selected}:
        reserve(outer, "upset")

    for row in candidates:
        if len(selected) >= max_tickets: break
        if row["combination"] in used: continue
        item = dict(row)
        item["type"] = "main" if sum(x["type"]=="main" for x in selected) < 6 else "deviation"
        selected.append(item); used.add(item["combination"])

    selected = selected[:max_tickets]
    total = sum(row["score"] for row in selected) or 1.0
    for row in selected:
        row["share"] = round(row["score"]/total, 4)
    return selected
