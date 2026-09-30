from __future__ import annotations

from itertools import permutations


COVERAGE_RULE_VERSION = "heiwajima_coverage_v1"
TRIFECTA_SCORE_POWER = 0.97


def ticket_count_for_coverage(top10_coverage: float) -> int:
    if top10_coverage >= 0.40:
        return 10
    if top10_coverage >= 0.35:
        return 12
    if top10_coverage >= 0.30:
        return 15
    return 18


def _fallback_pairs(boats: list[dict], head: int) -> dict[tuple[int, int], float]:
    by_lane = {int(boat["boat_no"]): boat for boat in boats}
    values = {
        (second, third): float(by_lane[second]["second_prob"]) * float(by_lane[third]["third_prob"])
        for second, third in permutations(range(1, 7), 2)
        if head not in (second, third)
    }
    total = sum(values.values()) or 1.0
    return {key: value / total for key, value in values.items()}


def generate_tickets(
    boats,
    scenarios,
    max_tickets=None,
    *,
    conditional_pairs=None,
    s16=None,
    s16_pairs=None,
):
    """Generate 10/12/15/18 tickets from the normalized 120-outcome model.

    ``max_tickets`` remains accepted for compatibility but is deliberately not
    used. Ticket count is determined only by probability coverage.
    """
    probabilities = {int(boat["boat_no"]): boat for boat in boats}
    conditional_pairs = conditional_pairs or {
        head: _fallback_pairs(boats, head) for head in range(1, 7)
    }

    candidates = []
    for first, second, third in permutations(range(1, 7), 3):
        # Conditional Placement is already reflected in P2/P3.  The small,
        # fixed calibration power prevents the 120-way distribution from
        # becoming spuriously sharp while preserving its ordering.
        score = (
            float(probabilities[first]["win_prob"])
            * float(probabilities[second]["second_prob"])
            * float(probabilities[third]["third_prob"])
        ) ** TRIFECTA_SCORE_POWER
        candidates.append({
            "combination": f"{first}-{second}-{third}",
            "first": first,
            "second": second,
            "third": third,
            "score": score,
        })
    total_score = sum(row["score"] for row in candidates) or 1.0
    for row in candidates:
        row["score"] /= total_score
    candidates.sort(key=lambda row: (-row["score"], row["combination"]))

    top10_coverage = sum(row["score"] for row in candidates[:10])
    selected_count = ticket_count_for_coverage(top10_coverage)
    selected = [dict(row) for row in candidates[:selected_count]]

    axis = max(probabilities, key=lambda lane: float(probabilities[lane]["win_prob"]))
    main_limit = max(6, int(round(selected_count * 0.60)))
    for index, row in enumerate(selected):
        if row["first"] != axis and (row["first"] >= 4 or index >= main_limit):
            row["type"] = "upset"
        elif index < main_limit:
            row["type"] = "main"
        else:
            row["type"] = "deviation"
        row["head_reserved"] = False
        row["predicted_probability"] = round(row["score"], 8)

    dedicated = None
    if str((s16 or {}).get("level") or "off") == "strong" and s16_pairs:
        (second, third), dedicated_score = max(
            s16_pairs.items(), key=lambda item: (item[1], -item[0][0], -item[0][1])
        )
        combo = f"6-{second}-{third}"
        duplicate = any(row["combination"] == combo for row in selected)
        dedicated = {
            "combination": combo,
            "score": round(float(dedicated_score), 8),
            "added": not duplicate,
        }
        if not duplicate:
            selected.append({
                "combination": combo,
                "first": 6,
                "second": second,
                "third": third,
                "score": float(probabilities[6]["win_prob"]) * float(dedicated_score),
                "predicted_probability": round(
                    float(probabilities[6]["win_prob"]) * float(dedicated_score), 8
                ),
                "type": "upset",
                "head_reserved": True,
                "s16_dedicated": True,
            })

    selected_total = sum(row["score"] for row in selected) or 1.0
    for row in selected:
        row["share"] = round(row["score"] / selected_total, 4)

    coverage_need = {
        "top10Coverage": round(top10_coverage, 6),
        "selectedTicketCount": selected_count,
        "ruleVersion": COVERAGE_RULE_VERSION,
    }
    return selected, coverage_need, dedicated
