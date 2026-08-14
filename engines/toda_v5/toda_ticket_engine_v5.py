from __future__ import annotations

import math
from toda_utils_v5 import LANES, num, normalize_map


def _norm_excluding(values, excluded):
    raw = {}
    for lane in LANES:
        k = str(lane)
        raw[k] = 0.0 if lane in excluded else max(0.0001, num((values or {}).get(k), 0.0))
    total = sum(raw.values())
    if total <= 0:
        allowed = [lane for lane in LANES if lane not in excluded]
        return {str(lane): (100.0 / len(allowed) if lane in allowed else 0.0) for lane in LANES}
    return {k: round(v * 100.0 / total, 1) for k, v in raw.items()}


def conditional_third(head, second, third_by_head):
    """Derive P(third | head, second) from head-conditioned third persistence.

    v5 stores thirdByHead only by winner. We preserve that source but remove the
    already-used first/second boats and renormalize. This gives a coherent
    three-stage ticket chain without inventing an odds signal.
    """
    src = (third_by_head or {}).get(str(head)) or {}
    return _norm_excluding(src, {int(head), int(second)})


def marginal_second(win, second_by_head):
    values = {str(lane): 0.0 for lane in LANES}
    for head in LANES:
        wh = num((win or {}).get(str(head)), 0.0) / 100.0
        sec = (second_by_head or {}).get(str(head)) or {}
        for lane in LANES:
            if lane == head:
                continue
            values[str(lane)] += wh * num(sec.get(str(lane)), 0.0)
    return normalize_map(values)


def marginal_third(win, second_by_head, third_by_head):
    values = {str(lane): 0.0 for lane in LANES}
    for head in LANES:
        wh = num((win or {}).get(str(head)), 0.0) / 100.0
        sec = (second_by_head or {}).get(str(head)) or {}
        for second in LANES:
            if second == head:
                continue
            ws = num(sec.get(str(second)), 0.0) / 100.0
            if ws <= 0:
                continue
            third = conditional_third(head, second, third_by_head)
            for lane in LANES:
                if lane in (head, second):
                    continue
                values[str(lane)] += wh * ws * num(third.get(str(lane)), 0.0)
    return normalize_map(values)


def combo_prob(combo, win, second_by_head, third_by_head):
    a, b, c = map(int, combo.split("-"))
    third = conditional_third(a, b, third_by_head)
    return round(
        num(win[str(a)])
        * num(second_by_head[str(a)][str(b)])
        * num(third[str(c)])
        / 10000,
        1,
    )


def build_head_conditionals(head, scores, scenario):
    links = (scenario or {}).get("links") or [x for x in LANES if x != head]
    rank_bonus = {str(l): max(0, .72 - i * .13) for i, l in enumerate(links)}
    second = {}
    third = {}
    for lane in LANES:
        k = str(lane)
        if lane == head:
            second[k] = .03
            third[k] = .03
            continue
        second[k] = math.exp(scores[k] * .34 + rank_bonus.get(k, 0))
        third[k] = math.exp(scores[k] * .22 + rank_bonus.get(k, 0) + (.14 if lane in (4, 5, 6) else 0))
    return normalize_map(second), normalize_map(third)


def _candidate_heads(win, sab):
    ranked = sorted(LANES, key=lambda x: num(win[str(x)]), reverse=True)
    head_limit = {"S": 1, "A": 2, "B": 3}.get(sab, 3)
    selected = ranked[:head_limit]
    # Every >=10% head is explicitly considered. It is added only if the SAB
    # structure still allows a meaningful alternate head, preventing ticket bloat.
    for lane in ranked:
        if num(win[str(lane)]) >= 10 and lane not in selected and len(selected) < 3:
            selected.append(lane)
    return selected


def _all_combos_for_head(head, win, second_by_head, third_by_head, scenarios):
    smap = {int(s["head"]): s for s in scenarios or []}
    scenario = smap.get(head, {})
    links = scenario.get("links") or [x for x in LANES if x != head]
    secs = sorted(
        [x for x in links if x != head],
        key=lambda x: num(second_by_head[str(head)][str(x)]),
        reverse=True,
    )
    rows = []
    for second in secs:
        third = conditional_third(head, second, third_by_head)
        for lane in LANES:
            if lane in (head, second):
                continue
            combo = f"{head}-{second}-{lane}"
            prob = combo_prob(combo, win, second_by_head, third_by_head)
            rows.append((prob, second, lane, combo))
    rows.sort(reverse=True)
    return rows


def _drift_second(head, second_by_head, public_second, public_third):
    ranked = sorted(
        [lane for lane in LANES if lane != head],
        key=lambda lane: num(second_by_head[str(head)][str(lane)]),
        reverse=True,
    )
    if len(ranked) < 3:
        return None
    best = num(second_by_head[str(head)][str(ranked[0])])
    candidates = ranked[2:4]
    candidates = [
        lane for lane in candidates
        if num(second_by_head[str(head)][str(lane)]) >= best * .32
        or num(public_second[str(lane)]) + num(public_third[str(lane)]) >= 22
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda lane: (
            num(second_by_head[str(head)][str(lane)])
            + .35 * num(public_second[str(lane)])
            + .20 * num(public_third[str(lane)])
        ),
    )


def build_tickets(win, second_by_head, third_by_head, scenarios, sab):
    public_second = marginal_second(win, second_by_head)
    public_third = marginal_third(win, second_by_head, third_by_head)
    heads = _candidate_heads(win, sab)
    limit = 6 if sab == "S" else 9 if sab == "A" else 10
    ranked_all = []
    by_head = {}
    for head in heads:
        rows = _all_combos_for_head(head, win, second_by_head, third_by_head, scenarios)
        by_head[head] = rows
        ranked_all.extend(rows)
    ranked_all.sort(reverse=True)

    selected = []
    seen = set()

    def add(combo, role):
        if combo in seen or len(selected) >= limit:
            return
        seen.add(combo)
        selected.append({
            "combo": combo,
            "role": role,
            "prob": combo_prob(combo, win, second_by_head, third_by_head),
            "odds": "-",
        })

    # Core conditional-probability tickets.
    core_target = max(4, limit - 2)
    for prob, second, third, combo in ranked_all:
        add(combo, "本線" if len(selected) < 3 else "展開保険")
        if len(selected) >= core_target:
            break

    # Clear-axis races keep one extra 2nd-place drift boat instead of filling all
    # tickets with only the two strongest seconds. This is the 8/14 7R lesson,
    # implemented structurally rather than fitting that result.
    axis = max(LANES, key=lambda lane: num(win[str(lane)]))
    if num(win[str(axis)]) >= 40:
        drift = _drift_second(axis, second_by_head, public_second, public_third)
        if drift is not None:
            third = conditional_third(axis, drift, third_by_head)
            thirds = sorted(
                [lane for lane in LANES if lane not in (axis, drift)],
                key=lambda lane: num(third[str(lane)]),
                reverse=True,
            )[:2]
            for lane in thirds:
                add(f"{axis}-{drift}-{lane}", "2着ズレ")

    # Fill any remaining slots by exact conditional trifecta probability.
    for prob, second, third, combo in ranked_all:
        add(combo, "展開保険")
        if len(selected) >= limit:
            break

    return selected[:limit]


def build_upset_tickets(win, second_by_head, third_by_head, scenarios):
    heads = sorted([x for x in LANES if x != 1], key=lambda x: num(win[str(x)]), reverse=True)[:2]
    out = []
    seen = set()
    for head in heads:
        rows = _all_combos_for_head(head, win, second_by_head, third_by_head, scenarios)
        for _, _, _, combo in rows[:4]:
            if combo in seen:
                continue
            seen.add(combo)
            out.append({
                "combo": combo,
                "role": "荒れ対応",
                "prob": combo_prob(combo, win, second_by_head, third_by_head),
                "odds": "-",
            })
    return out[:8]
