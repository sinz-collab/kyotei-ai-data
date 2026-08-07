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
    """Course-kimarite evidence used as scenario strength, never as a head lock."""
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


def detect_scenarios(racers, profiles, base_scores, context):
    by = {int(r["lane"]): r for r in racers}
    s = []
    one = by[1]
    one_profile = profiles["1"]
    inside = inside_profile(one)

    # Base 1-course reliability. Local 0.00 is missing, not literal zero.
    one_rel = (_local_win_or_national(one) - 4.8) * .42
    one_rel += clamp((.185 - _st(one, one_profile)) * 8, -.7, .7)
    one_rel += clamp(num(one_profile.get("top3_vs_course_avg"), 0) / 20, -.8, .8)
    if inside["escape"] > 0:
        one_rel += clamp((inside["escape"] - 48) / 35, -.65, .65)
        one_rel -= clamp((inside["vulnerability"] - 35) / 65, -.20, .50)

    one_weak = one_rel < -.15 or base_scores["1"] < max(base_scores[str(i)] for i in (2, 3, 4)) - .6

    if not one_weak or base_scores["1"] >= max(base_scores[str(i)] for i in (2, 3, 4)) - .25:
        s.append({
            "id": "IN_ESCAPE",
            "label": "1逃げ",
            "head": 1,
            "weight": clamp(.86 + one_rel * .22, .35, 1.28),
            "links": [2, 3, 4, 5, 6],
            "kimarite": inside,
        })

    r2 = by[2]
    p2 = profiles["2"]
    a2 = attack_profile(r2, 2)
    two_support = a2["headRate"] >= 6 or num(p2.get("win_rate"), 0) >= 12 or str(r2.get("class", "")).startswith("A")
    if _st(r2, p2) <= .19 and two_support:
        kim = clamp(a2["headRate"] / 20, 0, .45)
        s.append({
            "id": "TWO_SASHI",
            "label": "2差し",
            "head": 2,
            "weight": clamp((1.08 if one_weak else .62) + kim, .35, 1.30),
            "links": [1, 3, 4, 5, 6],
            "kimarite": a2,
        })

    r3 = by[3]
    p3 = profiles["3"]
    a3 = attack_profile(r3, 3)
    three_support = a3["headRate"] >= 5 or base_scores["3"] >= base_scores["2"] - .45
    if _st(r3, p3) <= .19 and three_support and base_scores["3"] >= base_scores["2"] - .80:
        label = "3まくり差し" if a3["makuriSashi"] > a3["makuri"] else "3攻め"
        kim = clamp(a3["headRate"] / 18, 0, .48)
        s.append({
            "id": "THREE_ATTACK",
            "label": label,
            "head": 3,
            "weight": clamp((1.08 if one_weak else .60) + kim, .30, 1.30),
            "links": [4, 5, 1, 2, 6],
            "kimarite": a3,
        })

    r4 = by[4]
    p4 = profiles["4"]
    a4 = attack_profile(r4, 4)
    four_support = a4["headRate"] >= 5 or base_scores["4"] >= base_scores["3"] - .45
    if _st(r4, p4) <= .19 and four_support and base_scores["4"] >= base_scores["3"] - .80:
        kim = clamp(a4["headRate"] / 18, 0, .48)
        s.append({
            "id": "FOUR_KADO",
            "label": "4カド攻め",
            "head": 4,
            "weight": clamp((1.04 if one_weak else .58) + kim, .30, 1.30),
            "links": [5, 6, 1, 3, 2],
            "kimarite": a4,
        })

    for lane in (5, 6):
        p = profiles[str(lane)]
        r = by[lane]
        a = attack_profile(r, lane)
        strong_base = base_scores[str(lane)] >= max(base_scores["2"], base_scores["3"], base_scores["4"]) - .25
        if strong_base and (num(p.get("win_rate"), 0) >= 10 or a["headRate"] >= 5):
            s.append({
                "id": f"OUTER_{lane}",
                "label": f"{lane}外攻め",
                "head": lane,
                "weight": clamp(.48 + a["headRate"] / 35, .35, .85),
                "links": [1, 3, 4, 2, 6 if lane == 5 else 5],
                "kimarite": a,
            })

    wind = num(context.get("wind_speed"), 0)
    wave = num(context.get("wave_height"), 0)
    phase = str(context.get("tide_phase") or "")
    for x in s:
        if wind >= 4 or wave >= 4:
            if x["head"] in (3, 4, 5):
                x["weight"] += .10
            if x["head"] == 1:
                x["weight"] -= .10
        if any(k in phase for k in ("干潮", "下げ止まり", "低潮")) and x["head"] in (3, 4):
            x["weight"] += .08
        if any(k in phase for k in ("上げ", "満潮")) and x["head"] == 1:
            x["weight"] += .06
        x["weight"] = clamp(x["weight"], .20, 1.35)
    return sorted(s, key=lambda x: x["weight"], reverse=True), one_weak
