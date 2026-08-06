from __future__ import annotations
from itertools import permutations

class TicketGenerator:
    def combination_probabilities(self, probs: dict) -> list[dict]:
        boats = probs["boats"]
        by_lane = {row["lane"]: row for row in boats}
        combos = []
        for a, b, c in permutations(sorted(by_lane), 3):
            pa = by_lane[a]["win"]
            remaining_second = sum(by_lane[x]["second"] for x in by_lane if x != a)
            pb = by_lane[b]["second"] / remaining_second if remaining_second else 0
            remaining_third = sum(by_lane[x]["third"] for x in by_lane if x not in (a, b))
            pc = by_lane[c]["third"] / remaining_third if remaining_third else 0
            combos.append({"ticket": f"{a}-{b}-{c}", "probability": pa * pb * pc})
        return sorted(combos, key=lambda x: x["probability"], reverse=True)

    def generate(self, probs: dict, scenarios: dict) -> dict:
        ranked = self.combination_probabilities(probs)
        by_lane = {int(row["lane"]): row for row in probs["boats"]}
        win_ranked = sorted(by_lane.values(), key=lambda row: row["win"], reverse=True)
        primary_head = int(win_ranked[0]["lane"])
        secondary_head = int(win_ranked[1]["lane"])
        secondary_win = float(win_ranked[1]["win"])

        # Base ticket buckets.
        main = ranked[:6]
        used = {x["ticket"] for x in main}

        deviation_candidates = []
        for item in main:
            a, b, c = map(int, item["ticket"].split("-"))
            for ticket in (f"{b}-{a}-{c}", f"{a}-{c}-{b}"):
                match = next((x for x in ranked if x["ticket"] == ticket), None)
                if match and ticket not in used:
                    deviation_candidates.append(match)
        deviation_candidates = sorted(
            {x["ticket"]: x for x in deviation_candidates}.values(),
            key=lambda x: x["probability"],
            reverse=True,
        )
        deviation = deviation_candidates[:2]
        used.update(x["ticket"] for x in deviation)

        upset = [x for x in ranked if x["ticket"] not in used][:2]

        # Guarantee the second-ranked head when it is materially live.
        min_secondary = 2 if secondary_win >= 0.18 else (1 if secondary_win >= 0.15 else 0)
        if min_secondary:
            current = [
                x for x in (main + deviation + upset)
                if int(x["ticket"].split("-")[0]) == secondary_head
            ]
            needed = max(0, min_secondary - len(current))
            secondary_candidates = [
                x for x in ranked
                if int(x["ticket"].split("-")[0]) == secondary_head
                and x["ticket"] not in {y["ticket"] for y in (main + deviation + upset)}
            ]

            # Prefer secondary-head combinations that retain the primary head in second,
            # then combinations with the strongest second/third linkage.
            def secondary_score(item: dict) -> tuple:
                a, b, c = map(int, item["ticket"].split("-"))
                primary_second_bonus = 1 if b == primary_head else 0
                return (
                    primary_second_bonus,
                    by_lane[b]["second"] + by_lane[c]["third"],
                    item["probability"],
                )

            secondary_candidates.sort(key=secondary_score, reverse=True)

            for candidate in secondary_candidates[:needed]:
                # Replace the weakest non-secondary-head ticket, preferring upset,
                # then deviation, then main. Never delete an existing secondary-head ticket.
                replaced = False
                for bucket in (upset, deviation, main):
                    eligible = [
                        (i, x) for i, x in enumerate(bucket)
                        if int(x["ticket"].split("-")[0]) != secondary_head
                    ]
                    if not eligible:
                        continue
                    i, weakest = min(eligible, key=lambda t: t[1]["probability"])
                    bucket[i] = {
                        **candidate,
                        "reason": "secondary_head_minimum"
                    }
                    replaced = True
                    break
                if not replaced:
                    break

        all_tickets = main + deviation + upset
        # Final de-duplication and fill to 10 if a forced replacement introduced overlap.
        unique = []
        seen = set()
        for item in all_tickets:
            if item["ticket"] not in seen:
                unique.append(item)
                seen.add(item["ticket"])
        for item in ranked:
            if len(unique) >= 10:
                break
            if item["ticket"] not in seen:
                unique.append(item)
                seen.add(item["ticket"])

        # Re-split while preserving 6/2/2 card structure.
        main = unique[:6]
        deviation = unique[6:8]
        upset = unique[8:10]
        return {
            "main": main,
            "deviation": deviation,
            "upset": upset,
            "all": unique[:10],
        }
