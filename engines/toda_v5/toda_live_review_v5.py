from copy import deepcopy
from toda_utils_v5 import LANES, num, clamp, normalize_map
from toda_ticket_engine_v5 import build_tickets, build_upset_tickets
from toda_sab_engine_v5 import judge_sab
from toda_scenario_engine_v5 import detect_scenarios, inside_profile, attack_profile


def _rows(doc, key):
    if not doc or doc.get("status") != "complete" or doc.get("complete") is not True:
        return None
    rows = (doc.get("data") or {}).get(key) or []
    return rows if len(rows) == 6 else None


def _rank(rows, key):
    vals = [(int(x["lane"]), num(x.get(key), 999)) for x in rows]
    vals.sort(key=lambda x: x[1])
    return {str(l): i + 1 for i, (l, _) in enumerate(vals)}


def _rank_score(rank):
    return {1: 1.0, 2: .78, 3: .50, 4: .25, 5: .05, 6: -.10}.get(int(rank), 0)


def _historical_st(r, profile):
    vals = [num(r.get("local_st"), 9), num(profile.get("avg_st"), 9), num(r.get("avg_st"), 9)]
    vals = [v for v in vals if 0 <= v < 1]
    return min(vals) if vals else .19


def _live_st_support(ex_st, hist_st):
    """Exhibition ST is corroboration only; it never acts alone."""
    if ex_st < 0:  # F in exhibition = strong step-in signal, not a penalty by itself.
        return 1.0 if hist_st <= .18 else .65
    if ex_st <= .08:
        return .90 if hist_st <= .18 else .55
    if ex_st <= .12:
        return .70 if hist_st <= .18 else .35
    if ex_st <= .17:
        return .35 if hist_st <= .18 else .10
    if ex_st >= .22 and hist_st >= .19:
        return -.30
    return 0.0


def _actual_course_map(doc):
    data = (doc or {}).get("data") or {}
    order = data.get("actual_entry") or []
    if len(order) != 6:
        return {lane: lane for lane in LANES}, False
    course_map = {int(lane): course for course, lane in enumerate(order, start=1)}
    changed = any(course_map.get(lane, lane) != lane for lane in LANES)
    return course_map, changed


def _adjust_conditionals(base_map, review, position, residual_boost=None):
    """Apply live strength to every head-conditioned opponent map, then renormalize."""
    residual_boost = residual_boost or {}
    out = {}
    for head in LANES:
        hk = str(head)
        src = (base_map or {}).get(hk) or {}
        vals = {}
        for lane in LANES:
            k = str(lane)
            if lane == head:
                vals[k] = max(.03, num(src.get(k), .03))
            else:
                vals[k] = max(.03, num(src.get(k), 0) + review[k][position] + num(residual_boost.get(k), 0))
        out[hk] = normalize_map(vals)
    return out


def _build_live_attack_meta(racers, profiles, exhibit, original, exrank, straight_rank, wind, wave):
    emap = {str(x["lane"]): x for x in exhibit}
    meta = {}
    one = next(r for r in racers if int(r["lane"]) == 1)
    inside = inside_profile(one)

    # The target vulnerability differs by attack route.
    for lane in LANES:
        k = str(lane)
        r = next(x for x in racers if int(x["lane"]) == lane)
        p = profiles.get(k) or {}
        ex = emap[k]
        hist_st = _historical_st(r, p)
        st_support = _live_st_support(num(ex.get("start_time"), .99), hist_st)
        ex_speed = _rank_score(exrank[k])
        straight = _rank_score(straight_rank[k]) if straight_rank and k in straight_rank else 0
        kim = attack_profile(r, lane) if lane != 1 else None

        if lane == 1:
            escape = inside["escape"]
            defense = (
                .34 * straight
                + .22 * ex_speed
                + .20 * max(0, st_support)
                + .24 * clamp((escape - 35) / 35, 0, 1)
            )
            meta[k] = {
                "headScore": 0.0,
                "residualScore": 0.0,
                "defenseScore": clamp(defense, 0, 1),
                "signals": [],
                "historicalST": hist_st,
                "exhibitionSTSupport": st_support,
                "inside": inside,
            }
            continue

        if lane == 2:
            vuln = clamp(inside["sashare"] / 30, 0, 1)
        elif lane in (3, 4):
            vuln = clamp((inside["makurare"] + inside["makurareZashi"]) / 45, 0, 1)
        else:
            vuln = clamp(inside["vulnerability"] / 65, 0, 1)

        kim_head = clamp((kim or {}).get("headRate", 0) / 14, 0, 1)
        outer_water = .12 if (wind >= 4 or wave >= 4) and lane in (3, 4, 5) else 0

        signals = []
        if straight >= .78: signals.append("straight")
        if st_support >= .55: signals.append("st")
        if ex_speed >= .78: signals.append("exhibition")
        if kim_head >= .45: signals.append("kimarite")
        if vuln >= .45: signals.append("inside_vulnerability")

        # Head promotion needs corroboration from multiple independent signals.
        head_score = (
            .31 * max(0, straight)
            + .20 * max(0, st_support)
            + .15 * max(0, ex_speed)
            + .20 * kim_head
            + .14 * vuln
            + outer_water
        )
        if len(signals) < 2:
            head_score *= .58
        elif len(signals) >= 4:
            head_score += .08
        head_score = clamp(head_score, 0, 1)

        # Good straight/exhibition but weak kimarite is more useful for 2nd/3rd than head.
        residual_score = clamp(
            .42 * max(0, straight)
            + .22 * max(0, ex_speed)
            + .18 * max(0, st_support)
            + .10 * kim_head
            + .08 * vuln,
            0,
            1,
        )
        meta[k] = {
            "headScore": head_score,
            "residualScore": residual_score,
            "signals": signals,
            "historicalST": hist_st,
            "exhibitionSTSupport": st_support,
            "straightRank": straight_rank.get(k) if straight_rank else None,
            "exhibitionRank": exrank.get(k),
            "kimarite": kim,
            "insideVulnerability": vuln,
        }
    return meta


def _apply_inside_breakdown(adjusted, attack_meta):
    defense = attack_meta["1"]["defenseScore"]
    attacks = sorted(
        [(lane, attack_meta[str(lane)]["headScore"]) for lane in (2, 3, 4, 5, 6)],
        key=lambda x: x[1],
        reverse=True,
    )
    top1 = attacks[0][1]
    top2 = attacks[1][1]
    # Multiple attackers matter, but 1's own live defense suppresses the penalty.
    collapse = clamp(top1 * .68 + top2 * .32 - defense * .55, 0, 1)
    penalty = clamp(collapse * 6.0, 0, 6.0)
    adjusted["win"]["1"] -= penalty

    total_head = sum(v for _, v in attacks) or 1
    for lane, score in attacks:
        if score <= 0:
            continue
        # Most of the removed 1-win mass is redistributed to credible attack heads.
        adjusted["win"][str(lane)] += penalty * .82 * score / total_head

    return collapse, penalty, attacks


def _live_scenarios(base_scenarios, racers, profiles, base_scores, context, attack_meta, collapse):
    scenarios, one_weak = detect_scenarios(racers, profiles, base_scores, context)
    by_head = {int(s["head"]): deepcopy(s) for s in scenarios}

    # Preserve a valid morning scenario if recalculation omitted it, then update with live evidence.
    for s in base_scenarios or []:
        by_head.setdefault(int(s["head"]), deepcopy(s))

    if 1 in by_head:
        by_head[1]["weight"] = clamp(by_head[1]["weight"] - collapse * .45 + attack_meta["1"]["defenseScore"] * .15, .20, 1.35)
        by_head[1]["liveDefense"] = attack_meta["1"]

    for lane in (2, 3, 4, 5, 6):
        k = str(lane)
        score = attack_meta[k]["headScore"]
        if score >= .42 and lane not in by_head:
            by_head[lane] = {
                "id": f"LIVE_ATTACK_{lane}",
                "label": f"{lane}直線/ST攻め",
                "head": lane,
                "weight": clamp(.42 + score * .60, .45, 1.20),
                "links": [x for x in LANES if x != lane],
            }
        if lane in by_head:
            by_head[lane]["weight"] = clamp(by_head[lane]["weight"] + score * .32, .20, 1.35)
            by_head[lane]["liveAttack"] = attack_meta[k]

    return sorted(by_head.values(), key=lambda x: x["weight"], reverse=True), one_weak


def apply_live_review(prediction, documents):
    direct = _rows(documents.get("direct"), "racers")
    exhibit = _rows(documents.get("exhibition"), "entries")
    if not direct or not exhibit:
        return False

    baseline = prediction.setdefault("_baseline", {
        "win": deepcopy(prediction["win"]),
        "second": deepcopy(prediction["second"]),
        "third": deepcopy(prediction["third"]),
        "secondByHead": deepcopy(prediction.get("secondByHead") or {}),
        "thirdByHead": deepcopy(prediction.get("thirdByHead") or {}),
        "scenarios": deepcopy(prediction.get("scenarios") or []),
    })
    baseline.setdefault("secondByHead", deepcopy(prediction.get("secondByHead") or {}))
    baseline.setdefault("thirdByHead", deepcopy(prediction.get("thirdByHead") or {}))
    baseline.setdefault("scenarios", deepcopy(prediction.get("scenarios") or []))

    exrank = _rank(exhibit, "exhibition_time")
    original = _rows(documents.get("original_exhibition"), "entries")
    original_key = "sum" if original and all(x.get("sum") not in (None, "") for x in original) else "lap_time"
    orank = _rank(original, original_key) if original else {}
    straight_rank = _rank(original, "straight_time") if original else {}
    dmap = {str(x["lane"]): x for x in direct}
    emap = {str(x["lane"]): x for x in exhibit}
    wind = num((documents["direct"].get("data") or {}).get("wind_speed"), 0)
    wave = num((documents["direct"].get("data") or {}).get("wave_height"), 0)

    model_inputs = prediction.get("modelInputs") or {}
    racers = deepcopy(model_inputs.get("racers") or [])
    profiles = deepcopy(model_inputs.get("profiles") or {})
    base_scores = deepcopy(model_inputs.get("baseScores") or {})
    course_map, entry_changed = _actual_course_map(documents.get("direct"))
    if racers:
        for r in racers:
            lane = int(r["lane"])
            r["actual_course"] = course_map.get(lane, lane)

    adjusted = {"win": {}, "second": {}, "third": {}}
    review = {}
    for lane in LANES:
        k = str(lane)
        ex = emap[k]
        dr = dmap[k]
        rank_strength = 3.5 - exrank[k]
        course = int(num(ex.get("exhibition_course"), course_map.get(lane, lane)))
        course_shift = clamp(lane - course, -2, 2)
        original_strength = 3.5 - orank[k] if k in orank else 0
        parts = -.32 if dr.get("parts_exchange") else 0
        weight = -min(.35, max(0, num(dr.get("weight_adjustment"), 0)) * .09)
        outer = .20 if (wind >= 4 or wave >= 4) and course in (3, 4, 5) else 0
        delta = {
            "win": clamp(rank_strength * .68 + course_shift * .40 + original_strength * .30 + parts + weight + outer, -5.5, 5.5),
            "second": clamp(rank_strength * .44 + course_shift * .23 + original_strength * .21 + parts * .5 + weight * .5 + outer * .7, -4, 4),
            "third": clamp(rank_strength * .29 + course_shift * .11 + original_strength * .15 + parts * .25 + weight * .25 + outer * .5, -3, 3),
        }
        review[k] = delta
        for pos in adjusted:
            adjusted[pos][k] = num(baseline[pos][k]) + delta[pos]

    attack_meta = None
    collapse = penalty = 0.0
    attacks = []
    residual_boost = {}
    scenarios = deepcopy(baseline.get("scenarios") or prediction.get("scenarios") or [])

    if racers and len(racers) == 6 and profiles and base_scores:
        attack_meta = _build_live_attack_meta(racers, profiles, exhibit, original, exrank, straight_rank, wind, wave)
        collapse, penalty, attacks = _apply_inside_breakdown(adjusted, attack_meta)
        for lane in LANES:
            k = str(lane)
            residual_boost[k] = 2.0 * attack_meta[k].get("residualScore", 0) if lane != 1 else 0
        context = {
            "wind_speed": wind,
            "wave_height": wave,
            "tide_phase": prediction.get("tidePhase") or "",
        }
        scenarios, _ = _live_scenarios(scenarios, racers, profiles, base_scores, context, attack_meta, collapse)

    for pos in adjusted:
        prediction[pos] = normalize_map(adjusted[pos])

    prediction["secondByHead"] = _adjust_conditionals(baseline.get("secondByHead"), review, "second", residual_boost)
    prediction["thirdByHead"] = _adjust_conditionals(baseline.get("thirdByHead"), review, "third", residual_boost)
    prediction["scenarios"] = scenarios

    sab, axis, gap = judge_sab(prediction["win"], scenarios, prediction["secondByHead"], prediction["thirdByHead"])
    prediction["sab"] = sab
    prediction["second"] = prediction["secondByHead"][str(axis)]
    prediction["third"] = prediction["thirdByHead"][str(axis)]
    prediction["confidence"] = round(clamp(47 + gap * 2 + (8 if sab == "S" else 3 if sab == "A" else 0), 40, 88))
    prediction["readability"] = {"axisLane": axis, "comment": f"主軸{axis}号艇／直前・決まり手・攻め崩れ再計算後"}
    prediction["ai"] = build_tickets(prediction["win"], prediction["secondByHead"], prediction["thirdByHead"], scenarios, sab)
    prediction["aiUpset"] = build_upset_tickets(prediction["win"], prediction["secondByHead"], prediction["thirdByHead"], scenarios)
    prediction["tickets"] = [x["combo"] for x in prediction["ai"]]

    prediction["probabilityReview"] = {}
    for k in map(str, LANES):
        prediction["probabilityReview"][k] = {
            "morningWin": baseline["win"][k],
            "morningSecond": baseline["second"][k],
            "morningThird": baseline["third"][k],
            "win": prediction["win"][k],
            "second": prediction["second"][k],
            "third": prediction["third"][k],
            "deltaWin": round(prediction["win"][k] - baseline["win"][k], 1),
            "deltaSecond": round(prediction["second"][k] - baseline["second"][k], 1),
            "deltaThird": round(prediction["third"][k] - baseline["third"][k], 1),
        }

    prediction["insideBreakdown"] = {
        "score": round(collapse, 3),
        "winPenaltyPoints": round(penalty, 2),
        "lane1Defense": round((attack_meta or {}).get("1", {}).get("defenseScore", 0), 3),
        "attackers": [
            {"lane": lane, "headScore": round(score, 3), "signals": (attack_meta or {}).get(str(lane), {}).get("signals", [])}
            for lane, score in attacks
        ],
    }
    prediction["entryChangedDetected"] = bool(entry_changed)
    prediction["actualCourseMap"] = {str(k): v for k, v in course_map.items()}
    prediction["liveAttackMeta"] = attack_meta or {}
    prediction["probabilityReviewStatus"] = "reviewed"
    prediction.setdefault("probabilityFlow", {}).update({
        "realtimeApplied": True,
        "reviewed": True,
        "reviewLabel": "確率補正・攻め崩れ・相手連動・SAB・買い目再計算済み",
    })
    prediction["predictionStage"] = {
        "label": "本予想",
        "statusText": "戸田v5：実進入・展示・直線・ST・決まり手・攻め崩れ・相手連動再計算済み",
        "badge": "本予想",
        "color": "green",
    }
    prediction["liveReviewMeta"] = {
        "oddsUsedForProbability": False,
        "oddsRequiredForReview": False,
        "exhibitionStartUsedAlone": False,
        "originalExhibitionApplied": bool(original),
        "straightApplied": bool(original),
        "kimariteScenarioApplied": bool(attack_meta),
        "insideBreakdownApplied": bool(attack_meta),
        "entryComparedDirectly": True,
        "entryChangedDetected": bool(entry_changed),
        "ticketsRegenerated": True,
        "headConditionalsRegenerated": True,
    }
    return True
