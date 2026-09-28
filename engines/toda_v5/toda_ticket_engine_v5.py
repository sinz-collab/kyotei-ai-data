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


def _drift_second(head, second_by_head, public_second, public_third, excluded=None):
    excluded = set(excluded or [])
    ranked = sorted(
        [lane for lane in LANES if lane != head],
        key=lambda lane: num(second_by_head[str(head)][str(lane)]),
        reverse=True,
    )
    best = num(second_by_head[str(head)][str(ranked[0])])
    candidates = [
        lane for lane in ranked
        if lane not in excluded
        and (
            num(second_by_head[str(head)][str(lane)]) >= best * .32
            or num(public_second[str(lane)]) + num(public_third[str(lane)]) >= 22
        )
    ]
    if not candidates:
        candidates = [lane for lane in ranked if lane not in excluded]
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


def _drift_third(head, core_rows, selected_combos, win, second_by_head, third_by_head, public_second, public_third):
    seen_pairs = set()
    for _, second, _, _ in core_rows:
        pair = (head, second)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        candidates = []
        conditional = conditional_third(head, second, third_by_head)
        available = [lane for lane in LANES if lane not in (head, second)]
        best = max(num(conditional[str(lane)]) for lane in available)
        for lane in available:
            combo = f"{head}-{second}-{lane}"
            if combo in selected_combos:
                continue
            conditional_value = num(conditional[str(lane)])
            public_sum = num(public_second[str(lane)]) + num(public_third[str(lane)])
            if conditional_value < best * .28 and num(public_third[str(lane)]) < 8 and public_sum < 18:
                continue
            score = conditional_value + .40 * num(public_third[str(lane)]) + .15 * num(public_second[str(lane)])
            candidates.append((score, combo))
        if candidates:
            return max(candidates)[1]
    return None


def _scenario_head_priority(head, main_head, win, scenarios):
    rows = [row for row in scenarios or [] if int(row.get("head", 0)) == head]
    weight = max([num(row.get("weight"), 0) for row in rows] or [0])
    score = num(win[str(head)]) / 100.0 + weight
    if main_head != 1 and head == 1 and any(row.get("id") == "CURRENT_FORM_INSIDE_RESISTANCE" for row in rows):
        score += 10
    if main_head == 1 and head != 1 and rows:
        score += 5 + max(num(row.get("attackEstablishment"), row.get("weight", 0)) for row in rows)
    if any(row.get("attackChain") for row in rows):
        score += 3
    return score


def build_upset_tickets(
    win,
    second_by_head,
    third_by_head,
    scenarios,
    exclude_combos=None,
    exclude_heads=None,
    limit=2,
    role="シナリオ穴",
    scenario_only=False,
):
    excluded_combos = set(exclude_combos or [])
    excluded_heads = {int(head) for head in (exclude_heads or [])}
    main_head = max(LANES, key=lambda lane: num(win[str(lane)]))
    scenario_heads = {int(row["head"]) for row in scenarios or []}
    heads = [
        lane for lane in LANES
        if lane != main_head
        and lane not in excluded_heads
        and (not scenario_only or lane in scenario_heads)
    ]
    heads.sort(
        key=lambda lane: _scenario_head_priority(lane, main_head, win, scenarios),
        reverse=True,
    )
    out = []
    seen = set(excluded_combos)
    for head in heads:
        rows = _all_combos_for_head(head, win, second_by_head, third_by_head, scenarios)
        for _, _, _, combo in rows:
            if combo in seen:
                continue
            seen.add(combo)
            out.append({
                "combo": combo,
                "role": role,
                "prob": combo_prob(combo, win, second_by_head, third_by_head),
                "odds": "-",
                "scenarioHead": head,
            })
            if len(out) >= limit:
                return out
    return out


def build_tickets(win, second_by_head, third_by_head, scenarios, sab):
    public_second = marginal_second(win, second_by_head)
    public_third = marginal_third(win, second_by_head, third_by_head)
    main_head = max(LANES, key=lambda lane: num(win[str(lane)]))
    ranked_main = _all_combos_for_head(main_head, win, second_by_head, third_by_head, scenarios)
    ranked_all = []
    for head in LANES:
        ranked_all.extend(_all_combos_for_head(head, win, second_by_head, third_by_head, scenarios))
    ranked_all.sort(reverse=True)

    selected = []
    seen = set()

    def add(combo, role):
        if combo in seen or len(selected) >= 10:
            return
        seen.add(combo)
        selected.append({
            "combo": combo,
            "role": role,
            "prob": combo_prob(combo, win, second_by_head, third_by_head),
            "odds": "-",
        })

    # Six core tickets ranked by exact conditional trifecta probability.
    core_rows = ranked_all[:6]
    for _, _, _, combo in core_rows:
        add(combo, "本線")

    main_core_rows = [row for row in core_rows if int(row[3].split("-")[0]) == main_head]
    used_seconds = {second for _, second, _, _ in main_core_rows}
    drift_second = _drift_second(main_head, second_by_head, public_second, public_third, used_seconds)
    if drift_second is not None:
        third = conditional_third(main_head, drift_second, third_by_head)
        drift_third = max(
            [lane for lane in LANES if lane not in (main_head, drift_second)],
            key=lambda lane: num(third[str(lane)]),
        )
        add(f"{main_head}-{drift_second}-{drift_third}", "2着ズレ")
    if len(selected) < 7:
        fallback = next(combo for _, _, _, combo in ranked_main if combo not in seen)
        add(fallback, "2着ズレ")

    drift_third = _drift_third(
        main_head, main_core_rows, seen, win, second_by_head, third_by_head, public_second, public_third
    )
    if drift_third is None:
        drift_third = next(combo for _, _, _, combo in ranked_main if combo not in seen)
    add(drift_third, "3着ズレ")

    selected.extend(build_upset_tickets(
        win,
        second_by_head,
        third_by_head,
        scenarios,
        exclude_combos=seen,
        limit=2,
        role="シナリオ穴",
    ))
    return selected[:10]
