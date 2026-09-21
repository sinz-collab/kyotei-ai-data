from toda_utils_v5 import LANES, num, clamp, normalize_map


TODA_LANE1_ESCAPE_BASELINE = 44.813005666705216
SCENARIO_MAX_SHARE = .15
STRONG_ATTACK_SCENARIO_MAX_SHARE = .20


def _st(r, profile):
    vals = [num(profile.get("avg_st"), 9), num(r.get("local_st"), 9), num(r.get("avg_st"), 9)]
    vals = [v for v in vals if 0 <= v < 1]
    return min(vals) if vals else .19


def _local_win_or_national(r):
    nat = num(r.get("nat_win"), 4.5)
    local = num(r.get("local_win"), 0)
    return local if local > 0 else nat


def _rate(r, key, default=0.0):
    return clamp(num(r.get(key), default), 0, 100)


def inside_profile(r):
    escape = _rate(r, "boaters_escape_rate")
    sashare = _rate(r, "boaters_sashare_rate")
    makurare = _rate(r, "boaters_makurare_rate")
    makurare_zashi = _rate(r, "boaters_makurare_zashi_rate")
    vulnerability = clamp(sashare + makurare + makurare_zashi, 0, 100)
    return {
        "escape": escape,
        "sashare": sashare,
        "makurare": makurare,
        "makurareZashi": makurare_zashi,
        "vulnerability": vulnerability,
    }


def inside_weakness(r, profile):
    """Lane-1 weakness led by escape rate, with deliberately small auxiliaries."""
    inside = inside_profile(r)
    escape = inside["escape"]
    escape_weakness = clamp(
        (TODA_LANE1_ESCAPE_BASELINE - escape) / TODA_LANE1_ESCAPE_BASELINE,
        0,
        1,
    ) if escape > 0 else 0.0
    st_aux = clamp((_st(r, profile) - .18) / .12, -1, 1) * .06
    course_aux = clamp(-num(profile.get("top3_vs_course_avg"), 0) / 30, -1, 1) * .06
    local_aux = clamp((4.8 - _local_win_or_national(r)) / 3.0, -1, 1) * .04
    strength = clamp(escape_weakness + st_aux + course_aux + local_aux, 0, 1)
    if escape >= 60:
        strength = min(strength, .25)
    elif escape >= 50:
        strength = min(strength, .45)
    return {
        "weakStrength": strength,
        "escapeRate": escape,
        "escapeBaseline": TODA_LANE1_ESCAPE_BASELINE,
        "escapeComponent": escape_weakness,
        "stAuxiliary": st_aux,
        "courseAuxiliary": course_aux,
        "localAuxiliary": local_aux,
    }


def attack_profile(r, lane):
    sashi = _rate(r, "boaters_sashi_rate")
    makuri = _rate(r, "boaters_makuri_rate")
    makuri_sashi = _rate(r, "boaters_makuri_sashi_rate")
    nigashi = _rate(r, "boaters_nigashi_rate")
    if lane == 2:
        head_rate = sashi + .35 * makuri + .20 * makuri_sashi
        style = "2差し"
    elif lane in (3, 4):
        head_rate = makuri + makuri_sashi + .25 * sashi
        style = f"{lane}攻め"
    else:
        head_rate = .55 * makuri + makuri_sashi + .20 * sashi
        style = f"{lane}外攻め"
    return {
        "headRate": clamp(head_rate, 0, 100),
        "sashi": sashi,
        "makuri": makuri,
        "makuriSashi": makuri_sashi,
        "nigashi": nigashi,
        "style": style,
    }


def _attack_evidence(lane, racer, profile, attack, weak_strength):
    kimarite_min = 6 if lane == 2 else 5
    course_win = num(profile.get("win_rate"), 0)
    course_fit = num(profile.get("top3_vs_course_avg"), 0)
    signals = {
        "kimarite": attack["headRate"] >= kimarite_min,
        "courseWin": course_win >= 10,
        "courseFit": course_fit >= 5,
        "st": _st(racer, profile) <= .18,
        "insideWeakness": weak_strength >= .35,
    }
    evidence_count = sum(1 for matched in signals.values() if matched)
    head_eligible = (signals["kimarite"] or signals["courseWin"]) and evidence_count >= 3
    if attack["headRate"] <= 0 and course_win <= 0:
        head_eligible = False
    contradictions = []
    if attack["headRate"] <= 0:
        contradictions.append("kimarite_zero")
    if course_win <= 0:
        contradictions.append("course_win_zero")
    if course_fit <= -12:
        contradictions.append("course_fit_negative")
    if _st(racer, profile) > .19:
        contradictions.append("st_slow")
    return {"signals": signals, "evidenceCount": evidence_count, "headEligible": head_eligible, "contradictions": contradictions}


def _water_scenario_modifier(head, context):
    """Water modifies scenario establishment, never a standalone lane probability."""
    wind = num(context.get("wind_speed"), 0)
    wave = num(context.get("wave_height"), 0)
    direction = int(num(context.get("wind_direction"), 0))
    phase = str(context.get("tide_phase") or "")
    delta = 0.0
    if wind >= 4 or wave >= 4:
        if head in (3, 4, 5):
            delta += .10
        if head == 1:
            delta -= .10
    if any(k in phase for k in ("干潮", "下げ止まり", "低潮")) and head in (3, 4):
        delta += .08
    if any(k in phase for k in ("上げ", "満潮")) and head == 1:
        delta += .06
    # Direction codes vary by collector. Use only a tiny scenario-level nudge;
    # no direction is strong enough here to create a head by itself.
    if direction not in (0, 17) and wind >= 3:
        if head in (3, 4):
            delta += .03
        elif head == 1:
            delta -= .02
    return delta


def detect_scenarios(racers, profiles, base_scores, context):
    by = {int(r["lane"]): r for r in racers}
    s = []
    one = by[1]
    one_profile = profiles["1"]
    inside = inside_profile(one)
    weakness = inside_weakness(one, one_profile)
    weak_strength = weakness["weakStrength"]
    escape_rel = (
        (inside["escape"] - TODA_LANE1_ESCAPE_BASELINE) / TODA_LANE1_ESCAPE_BASELINE
        if inside["escape"] > 0 else 0.0
    )
    auxiliary_weakness = weak_strength - weakness["escapeComponent"]
    one_rel = clamp(escape_rel - auxiliary_weakness, -.65, .65)
    one_weak = weak_strength >= .35

    if not one_weak or base_scores["1"] >= max(base_scores[str(i)] for i in (2, 3, 4)) - .25:
        s.append({"id": "IN_ESCAPE", "label": "1逃げ", "head": 1, "weight": clamp(.86 + one_rel * .22, .35, 1.28), "links": [2, 3, 4, 5, 6], "kimarite": inside})

    attack_scenarios = []
    for lane in (2, 3, 4):
        r = by[lane]
        p = profiles[str(lane)]
        a = attack_profile(r, lane)
        e = _attack_evidence(lane, r, p, a, weak_strength)
        if lane == 2:
            eligible = e["headEligible"]
            label = "2差し"
            base = 1.08 if one_weak else .62
            kim = clamp(a["headRate"] / 20, 0, .45)
            links = [1, 3, 4, 5, 6]
            sid = "TWO_SASHI"
        elif lane == 3:
            eligible = e["headEligible"] and base_scores["3"] >= base_scores["2"] - .80
            label = "3まくり差し" if a["makuriSashi"] > a["makuri"] else "3攻め"
            base = 1.08 if one_weak else .60
            kim = clamp(a["headRate"] / 18, 0, .48)
            links = [4, 5, 1, 2, 6]
            sid = "THREE_ATTACK"
        else:
            eligible = e["headEligible"] and base_scores["4"] >= base_scores["3"] - .80
            label = "4カド攻め"
            base = 1.04 if one_weak else .58
            kim = clamp(a["headRate"] / 18, 0, .48)
            links = [5, 6, 1, 3, 2]
            sid = "FOUR_KADO"
        if eligible:
            row = {"id": sid, "label": label, "head": lane, "weight": clamp(base + kim, .30, 1.00), "links": links, "kimarite": a, "evidence": e}
            s.append(row)
            attack_scenarios.append(row)

    # 5/6 head is gated by an established inner attack chain. A weak lane1 alone
    # never promotes an outside winner. No hard cap is applied once the chain and
    # the outside boat's own course/kimarite evidence are both strong.
    chain_strength = max([num(x.get("weight"), 0) for x in attack_scenarios] or [0])
    for lane in (5, 6):
        p = profiles[str(lane)]
        r = by[lane]
        a = attack_profile(r, lane)
        strong_base = base_scores[str(lane)] >= max(base_scores["2"], base_scores["3"], base_scores["4"]) - .25
        own_head = num(p.get("win_rate"), 0) >= 10 or a["headRate"] >= 5
        if chain_strength >= .72 and strong_base and own_head:
            s.append({
                "id": f"OUTER_{lane}",
                "label": f"{lane}外連動",
                "head": lane,
                "weight": clamp(.30 + chain_strength * .25 + a["headRate"] / 40, .28, 1.00),
                "links": [1, 3, 4, 2, 6 if lane == 5 else 5],
                "kimarite": a,
                "attackChain": True,
            })

    for x in s:
        max_weight = 1.35 if int(x["head"]) == 1 else 1.00
        x["weight"] = clamp(x["weight"] + _water_scenario_modifier(int(x["head"]), context), .20, max_weight)
        if int(x["head"]) != 1:
            x["attackEstablishment"] = clamp(x["weight"], 0, 1)
            x.setdefault("kimariteMatchup", 0.0)
    return sorted(s, key=lambda x: x["weight"], reverse=True), one_weak


def apply_scenario_mix(base_probabilities, scenarios, consensus_lane=None):
    """Mix scenario heads once, with a hard total share and optional consensus guard."""
    rows = [x for x in scenarios or [] if num(x.get("weight"), 0) > 0]
    attacks = [x for x in rows if int(x.get("head", 0)) != 1]
    strong_attack = any(
        num(x.get("attackEstablishment"), 0) >= .85
        and num(x.get("kimariteMatchup"), 0) >= .60
        for x in attacks
    )
    max_share = STRONG_ATTACK_SCENARIO_MAX_SHARE if strong_attack else SCENARIO_MAX_SHARE
    total_weight = sum(num(x.get("weight"), 0) for x in rows)
    head_shares = {str(lane): 0.0 for lane in LANES}
    suppressed = []
    for row in rows:
        head = int(row["head"])
        before = max_share * num(row.get("weight"), 0) / total_weight if total_weight else 0.0
        establishment = num(row.get("attackEstablishment"), 0)
        matchup = num(row.get("kimariteMatchup"), 0)
        after = before
        guarded = consensus_lane is not None and head != int(consensus_lane)
        suppression_rate = 0.0
        if guarded:
            if establishment >= .85 and matchup >= .60:
                suppression_rate = .20
            elif establishment >= .70 and matchup >= .45:
                suppression_rate = .30
            else:
                suppression_rate = .40
            after *= 1 - suppression_rate
            suppressed.append(head)
        row["scenarioShareBeforeGuard"] = before
        row["scenarioShareAfterConsensusGuard"] = after
        row["scenarioShare"] = after
        row["scenarioGuardSuppressed"] = guarded
        row["scenarioGuardSuppressionRate"] = suppression_rate
        head_shares[str(head)] += after

    def mix_with_scale(scale):
        scaled = {k: v * scale for k, v in head_shares.items()}
        applied = sum(scaled.values())
        values = {
            str(lane): num(base_probabilities.get(str(lane)), 0) * (1 - applied)
            + scaled[str(lane)] * 100
            for lane in LANES
        }
        return normalize_map(values), scaled, applied

    base_normalized = normalize_map(base_probabilities)
    base_top = max(base_normalized, key=base_normalized.get)
    mixed_full, _, _ = mix_with_scale(1.0)
    scenario_top = max(mixed_full, key=mixed_full.get)
    flip_attempted = scenario_top != base_top
    flip_margin = mixed_full[scenario_top] - mixed_full[base_top] if flip_attempted else 0.0
    new_top_rows = [x for x in rows if str(int(x["head"])) == scenario_top]
    strong_new_top = any(
        num(x.get("attackEstablishment"), 0) >= .85
        and num(x.get("kimariteMatchup"), 0) >= .60
        for x in new_top_rows
    )
    flip_allowed = flip_attempted and strong_new_top and flip_margin >= 2.0
    flip_scale = 1.0
    if flip_attempted and not flip_allowed:
        low, high = 0.0, 1.0
        for _ in range(50):
            middle = (low + high) / 2
            candidate, _, _ = mix_with_scale(middle)
            if max(candidate, key=candidate.get) == base_top:
                low = middle
            else:
                high = middle
        flip_scale = low

    mixed, final_head_shares, applied_share = mix_with_scale(flip_scale)
    for row in rows:
        row["scenarioShare"] = row["scenarioShareAfterConsensusGuard"] * flip_scale
    correction = {
        str(lane): mixed[str(lane)] - num(base_probabilities.get(str(lane)), 0)
        for lane in LANES
    }
    return mixed, {
        "maxShare": max_share,
        "appliedShare": applied_share,
        "strongAttack": strong_attack,
        "consensusLane": int(consensus_lane) if consensus_lane is not None else None,
        "guardSuppressedLanes": sorted(set(suppressed)),
        "headShares": final_head_shares,
        "correctionPoints": correction,
        "baseTop": int(base_top),
        "scenarioTopBeforeFlipGuard": int(scenario_top),
        "finalTop": int(max(mixed, key=mixed.get)),
        "topFlipAttempted": flip_attempted,
        "topFlipAllowed": flip_allowed,
        "topFlipMarginBeforeGuard": flip_margin,
        "topFlipScale": flip_scale,
    }
