from __future__ import annotations
import math

class SabEngine:
    def __init__(self, config: dict):
        self.thresholds = config["sab"]

    def calculate(self, probs: dict, scenarios: dict, data_quality: dict) -> dict:
        win = sorted(probs["win"], reverse=True)
        head_margin = win[0] - win[1]
        head_score = min(25, 25 * head_margin / 0.25)
        scenario_values = sorted(scenarios["probabilities"].values(), reverse=True)
        scenario_score = min(20, 20 * (scenario_values[0] - scenario_values[1]) / 0.25)
        top3_stability = sum(sorted((b["top3"] for b in probs["boats"]), reverse=True)[:3]) / 3
        linkage_score = 20 * min(1, top3_stability)
        completeness = float(data_quality.get("completeness", 1.0))
        quality_score = 20 * completeness
        conflict_penalty = 0
        lane1 = next((b for b in probs["boats"] if b["lane"] == 1), None)
        if lane1 and lane1["win"] > 0.35 and len(lane1["risks"]) >= 2:
            conflict_penalty += 12
        if completeness < 0.90:
            conflict_penalty += 8
        if completeness < 0.80:
            conflict_penalty += 7
        score = max(0, min(100, 15 + head_score + scenario_score + linkage_score + quality_score - conflict_penalty))
        if score >= self.thresholds["S"]:
            rank = "S"
        elif score >= self.thresholds["A"]:
            rank = "A"
        elif score >= self.thresholds["B"]:
            rank = "B"
        else:
            rank = "見"
        return {
            "rank": rank,
            "score": round(score, 1),
            "components": {
                "head_stability": round(head_score, 2),
                "scenario_concentration": round(scenario_score, 2),
                "linkage": round(linkage_score, 2),
                "data_quality": round(quality_score, 2),
                "conflict_penalty": conflict_penalty,
            },
            "independent_of_ticket_count": True,
            "independent_of_odds": True,
        }
