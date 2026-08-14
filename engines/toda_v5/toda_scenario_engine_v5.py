from toda_utils_v5 import LANES, num, clamp


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


def _attack_evidence(lane, racer, profile, attack, inside, one_weak):
    kimarite_min = 6 if lane == 2 else 5
    course_win = num(profile.get("win_rate"), 0)
    course_fit = num(profile.get("top3_vs_course_avg"), 0)
    if lane == 2:
        inside_break = one_weak or inside["sashare"] >= 12
    else:
        inside_break = one_weak or inside["makurare"] + inside["makurareZashi"] >= 18
    signals = {
        "kimarite": attack["headRate"] >= kimarite_min,
        "courseWin": course_win >= 10,
        "courseFit": course_fit >= 5,
        "st": _st(racer, profile) <= .18,
        "insideBreak": inside_break,
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

    one_rel = (_local_win_or_national(one) - 4.8) * .42
    one_rel += clamp((.185 - _st(one, one_profile)) * 8, -.7, .7)
    one_rel += clamp(num(one_profile.get("top3_vs_course_avg"), 0) / 20, -.8, .8)
    if inside["escape"] > 0:
        one_rel += clamp((inside["escape"] - 48) / 35, -.65, .65)
        one_rel -= clamp((inside["vulnerability"] - 35) / 65, -.20, .50)
    one_weak = one_rel < -.15 or base_scores["1"] < max(base_scores[str(i)] for i in (2, 3, 4)) - .6

    if not one_weak or base_scores["1"] >= max(base_scores[str(i)] for i in (2, 3, 4)) - .25:
        s.append({"id": "IN_ESCAPE", "label": "1逃げ", "head": 1, "weight": clamp(.86 + one_rel * .22, .35, 1.28), "links": [2, 3, 4, 5, 6], "kimarite": inside})

    attack_scenarios = []
    for lane in (2, 3, 4):
        r = by[lane]
        p = profiles[str(lane)]
        a = attack_profile(r, lane)
        e = _attack_evidence(lane, r, p, a, inside, one_weak)
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
            row = {"id": sid, "label": label, "head": lane, "weight": clamp(base + kim, .30, 1.30), "links": links, "kimarite": a, "evidence": e}
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
        x["weight"] = clamp(x["weight"] + _water_scenario_modifier(int(x["head"]), context), .20, 1.35)
    return sorted(s, key=lambda x: x["weight"], reverse=True), one_weak
