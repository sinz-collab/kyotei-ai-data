from copy import deepcopy

from toda_utils_v5 import LANES, num, clamp, normalize_map
from toda_ticket_engine_v5 import build_tickets, build_upset_tickets, marginal_second, marginal_third
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
    if ex_st < 0:
        return .75 if hist_st <= .18 else .40
    if ex_st <= .08:
        return .65 if hist_st <= .18 else .35
    if ex_st <= .12:
        return .50 if hist_st <= .18 else .25
    if ex_st <= .17:
        return .25 if hist_st <= .18 else .05
    if ex_st >= .22 and hist_st >= .19:
        return -.20
    return 0.0


def _actual_course_map(doc):
    data = (doc or {}).get("data") or {}
    order = data.get("actual_entry") or []
    if len(order) != 6:
        return {lane: lane for lane in LANES}, False
    course_map = {int(lane): course for course, lane in enumerate(order, start=1)}
    changed = any(course_map.get(lane, lane) != lane for lane in LANES)
    return course_map, changed


def _refresh_changed_profiles(racers, profiles, course_map, master=None):
    refreshed = []
    for r in racers:
        lane = int(r["lane"])
        previous_course = int(r.get("actual_course") or r.get("entry_course") or lane)
        actual_course = int(course_map.get(lane, lane))
        if actual_course != previous_course:
            if master is None:
                from toda_master_loader_v5 import TodaMasterV5
                master = TodaMasterV5()
            profiles[str(lane)] = master.course_profile(r, actual_course)
            refreshed.append(lane)
        r["actual_course"] = actual_course
    return refreshed


def _original_metrics(original, exhibit):
    if not original:
        return {}, {}, {}, {}, {}, {}
    emap = {str(x["lane"]): x for x in exhibit}
    prepared = []
    for row in original:
        x = dict(row)
        k = str(x["lane"])
        lap = num(x.get("lap_time"), 999)
        ex = num(x.get("sum_exhibition"), num(emap.get(k, {}).get("exhibition_time"), 999))
        total = num(x.get("sum"), lap + ex)
        x["_sum"] = total
        x["_sum_diff"] = num(x.get("sum_difference"), 0)
        prepared.append(x)
    return (
        _rank(prepared, "_sum"),
        _rank(prepared, "turn_time"),
        _rank(prepared, "straight_time"),
        _rank(prepared, "lap_time"),
        {str(x["lane"]): num(x.get("_sum_diff"), 0) for x in prepared},
        {str(x["lane"]): x for x in prepared},
    )


def _matchup_score(lane, racer, inside, turn_s, sum_s, straight_s, profile):
    """Opponent weakness x own kimarite x live foot x course fit.

    5/6 intentionally get no direct lane1-vulnerability head score here.
    """
    attack = attack_profile(racer, lane) if lane != 1 else None
    course_fit = clamp((num(profile.get("top3_vs_course_avg"), 0) + 10) / 30, 0, 1)
    if lane == 2:
        vuln = clamp(inside["sashare"] / 30, 0, 1)
        own = clamp((attack or {}).get("sashi", 0) / 18, 0, 1)
        foot = clamp(.45 * max(0, turn_s) + .35 * max(0, sum_s) + .20 * course_fit, 0, 1)
    elif lane in (3, 4):
        makuri_match = clamp(inside["makurare"] / 28, 0, 1) * clamp((attack or {}).get("makuri", 0) / 16, 0, 1)
        ms_match = clamp(inside["makurareZashi"] / 28, 0, 1) * clamp((attack or {}).get("makuriSashi", 0) / 16, 0, 1)
        vuln = max(makuri_match, ms_match)
        own = 1.0
        foot = clamp(.34 * max(0, turn_s) + .26 * max(0, sum_s) + .24 * max(0, straight_s) + .16 * course_fit, 0, 1)
    else:
        return 0.0
    return clamp(vuln * own * foot, 0, 1)


def _build_live_meta(racers, profiles, exhibit, original, exrank, wind, wave):
    emap = {str(x["lane"]): x for x in exhibit}
    sum_rank, turn_rank, straight_rank, lap_rank, sum_diff, omap = _original_metrics(original, exhibit)
    meta = {}
    one = next(r for r in racers if int(r["lane"]) == 1)
    inside = inside_profile(one)

    for lane in LANES:
        k = str(lane)
        racer = next(x for x in racers if int(x["lane"]) == lane)
        profile = profiles.get(k) or {}
        ex = emap[k]
        hist_st = _historical_st(racer, profile)
        st_support = _live_st_support(num(ex.get("start_time"), .99), hist_st)
        ex_s = _rank_score(exrank[k])
        sum_s = _rank_score(sum_rank[k]) if k in sum_rank else 0
        turn_s = _rank_score(turn_rank[k]) if k in turn_rank else 0
        straight_s = _rank_score(straight_rank[k]) if k in straight_rank else 0
        lap_s = _rank_score(lap_rank[k]) if k in lap_rank else 0
        diff = num(sum_diff.get(k), 0)

        if lane == 1:
            defense = clamp(.28 * max(0, sum_s) + .24 * max(0, turn_s) + .16 * max(0, straight_s) + .12 * max(0, lap_s) + .10 * max(0, st_support) + .10 * clamp((inside["escape"] - 35) / 35, 0, 1), 0, 1)
            meta[k] = {"headScore": 0.0, "residualScore": clamp(.55 * max(0, sum_s) + .45 * max(0, turn_s), 0, 1), "defenseScore": defense, "matchupScore": 0.0, "sumRank": sum_rank.get(k), "turnRank": turn_rank.get(k), "straightRank": straight_rank.get(k), "lapRank": lap_rank.get(k), "sumDifference": diff, "historicalST": hist_st, "exhibitionSTSupport": st_support, "inside": inside}
            continue

        matchup = _matchup_score(lane, racer, inside, turn_s, sum_s, straight_s, profile)
        attack = attack_profile(racer, lane)
        own_head = clamp(attack.get("headRate", 0) / 14, 0, 1)
        # Straight is stronger for persistence than for head. Head needs combined
        # sum/turn/course/matchup evidence, especially for lanes 2/3/4.
        head_score = clamp(.22 * max(0, sum_s) + .20 * max(0, turn_s) + .10 * max(0, straight_s) + .08 * max(0, lap_s) + .08 * max(0, ex_s) + .08 * max(0, st_support) + .14 * own_head + .10 * matchup, 0, 1)
        residual_score = clamp(.30 * max(0, sum_s) + .27 * max(0, turn_s) + .20 * max(0, straight_s) + .13 * max(0, lap_s) + .06 * max(0, ex_s) + .04 * max(0, st_support), 0, 1)
        meta[k] = {"headScore": head_score, "residualScore": residual_score, "matchupScore": matchup, "sumRank": sum_rank.get(k), "turnRank": turn_rank.get(k), "straightRank": straight_rank.get(k), "lapRank": lap_rank.get(k), "sumDifference": diff, "historicalST": hist_st, "exhibitionSTSupport": st_support, "kimarite": attack}

    # Outside head requires a 2/3/4 attack chain. No hard cap once evidence aligns.
    chain = max(meta[str(lane)]["headScore"] + .35 * meta[str(lane)]["matchupScore"] for lane in (2, 3, 4))
    for lane in (5, 6):
        k = str(lane)
        own = meta[k]
        own["attackChainScore"] = chain
        own["headScore"] = clamp(own["headScore"] * clamp((chain - .25) / .55, 0, 1), 0, 1)
        own["residualScore"] = clamp(own["residualScore"] + .12 * chain, 0, 1)
    return meta


def _review_deltas(racers, exhibit, direct, meta):
    exrank = _rank(exhibit, "exhibition_time")
    emap = {str(x["lane"]): x for x in exhibit}
    dmap = {str(x["lane"]): x for x in direct}
    review = {}
    for lane in LANES:
        k = str(lane)
        m = meta[k]
        ex_s = _rank_score(exrank[k])
        sum_s = _rank_score(m.get("sumRank")) if m.get("sumRank") else 0
        turn_s = _rank_score(m.get("turnRank")) if m.get("turnRank") else 0
        straight_s = _rank_score(m.get("straightRank")) if m.get("straightRank") else 0
        lap_s = _rank_score(m.get("lapRank")) if m.get("lapRank") else 0
        parts = -.32 if dmap[k].get("parts_exchange") else 0
        weight = -min(.35, max(0, num(dmap[k].get("weight_adjustment"), 0)) * .09)
        matchup = num(m.get("matchupScore"), 0)
        diff_s = clamp(num(m.get("sumDifference"), 0) / .55, -1, 1)
        # Probability-point correction ranges: normal +/-1-3, multi-signal +/-4-6.
        # No single live signal can create a large head move by itself.
        win = .45 * sum_s + .30 * diff_s + .35 * turn_s + .18 * straight_s + .28 * lap_s + .18 * ex_s + 1.7 * matchup + 1.0 * num(m.get("headScore"), 0) + parts + weight
        second = .58 * sum_s + .38 * diff_s + .82 * turn_s + .55 * straight_s + .32 * lap_s + .16 * ex_s + .95 * matchup + 1.15 * num(m.get("residualScore"), 0) + parts * .5 + weight * .5
        third = .50 * sum_s + .32 * diff_s + .68 * turn_s + .48 * straight_s + .28 * lap_s + .12 * ex_s + .55 * matchup + .95 * num(m.get("residualScore"), 0) + parts * .25 + weight * .25
        review[k] = {"win": clamp(win, -5.5, 5.5), "second": clamp(second, -4.5, 4.5), "third": clamp(third, -3.8, 3.8)}
    return review


def _inside_breakdown(adjusted, meta):
    defense = meta["1"]["defenseScore"]
    attacks = sorted([(lane, meta[str(lane)]["headScore"] + .35 * meta[str(lane)].get("matchupScore", 0)) for lane in (2, 3, 4, 5, 6)], key=lambda x: x[1], reverse=True)
    top1, top2 = attacks[0][1], attacks[1][1]
    collapse = clamp(top1 * .68 + top2 * .32 - defense * .55, 0, 1)
    penalty = clamp(collapse * 5.5, 0, 5.5)
    adjusted["win"]["1"] -= penalty
    total = sum(v for _, v in attacks) or 1
    for lane, score in attacks:
        adjusted["win"][str(lane)] += penalty * .82 * score / total
    return collapse, penalty, attacks


def _residual_boosts(meta, scenarios):
    by_head = {str(head): {str(lane): 0.0 for lane in LANES} for head in LANES}
    scenario_map = {int(s["head"]): s for s in scenarios or []}
    defense = num(meta["1"].get("defenseScore"), 0)
    for head in LANES:
        if head == 1:
            continue
        for lane in LANES:
            if lane != head:
                by_head[str(head)][str(lane)] += 1.6 * num(meta[str(lane)].get("residualScore"), 0)
        if head == 2:
            by_head["2"]["1"] += 2.0 * defense
        elif head == 3:
            label = str((scenario_map.get(3) or {}).get("label") or "")
            by_head["3"]["1"] += (1.25 if "まくり差し" in label else .55) * defense
            by_head["3"]["4"] += .45 * num(meta["4"].get("residualScore"), 0)
        elif head == 4:
            by_head["4"]["1"] += .80 * defense
            by_head["4"]["5"] += .60 * num(meta["5"].get("residualScore"), 0)
            by_head["4"]["6"] += .45 * num(meta["6"].get("residualScore"), 0)
    return by_head


def _adjust_conditionals(base_map, review, position, residual_by_head):
    out = {}
    for head in LANES:
        hk = str(head)
        src = (base_map or {}).get(hk) or {}
        vals = {}
        for lane in LANES:
            k = str(lane)
            if lane == head:
                vals[k] = .03
            else:
                vals[k] = max(.03, num(src.get(k), 0) + review[k][position] + num((residual_by_head.get(hk) or {}).get(k), 0))
        out[hk] = normalize_map(vals)
    return out


def _live_scenarios(base_scenarios, racers, profiles, base_scores, context, meta, collapse):
    scenarios, one_weak = detect_scenarios(racers, profiles, base_scores, context)
    by_head = {int(s["head"]): deepcopy(s) for s in scenarios}
    for s in base_scenarios or []:
        by_head.setdefault(int(s["head"]), deepcopy(s))
    if 1 in by_head:
        by_head[1]["weight"] = clamp(by_head[1]["weight"] - collapse * .42 + meta["1"]["defenseScore"] * .15, .20, 1.35)
    chain = max(meta[str(lane)]["headScore"] + .35 * meta[str(lane)].get("matchupScore", 0) for lane in (2, 3, 4))
    for lane in (2, 3, 4, 5, 6):
        k = str(lane)
        score = meta[k]["headScore"] + .35 * meta[k].get("matchupScore", 0)
        if lane in (5, 6) and chain < .42:
            score = 0
        if score >= .42 and lane not in by_head:
            by_head[lane] = {"id": f"LIVE_ATTACK_{lane}", "label": f"{lane}直前攻め", "head": lane, "weight": clamp(.42 + score * .60, .45, 1.20), "links": [x for x in LANES if x != lane]}
        if lane in by_head:
            by_head[lane]["weight"] = clamp(num(by_head[lane].get("weight"), .4) + score * .30, .20, 1.35)
            by_head[lane]["liveAttack"] = meta[k]
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
    original = _rows(documents.get("original_exhibition"), "entries") or []
    exrank = _rank(exhibit, "exhibition_time")
    model_inputs = prediction.get("modelInputs") or {}
    racers = deepcopy(model_inputs.get("racers") or [])
    profiles = deepcopy(model_inputs.get("profiles") or {})
    base_scores = deepcopy(model_inputs.get("baseScores") or {})
    course_map, entry_changed = _actual_course_map(documents.get("direct"))
    if racers:
        actual_entry = ((documents.get("direct") or {}).get("data") or {}).get("actual_entry") or []
        if len(actual_entry) == 6:
            _refresh_changed_profiles(racers, profiles, course_map)
        else:
            for r in racers:
                r["actual_course"] = course_map.get(int(r["lane"]), int(r["lane"]))

    ddata = (documents.get("direct") or {}).get("data") or {}
    wind = num(ddata.get("wind_speed"), 0)
    wave = num(ddata.get("wave_height"), 0)
    wind_direction = num(ddata.get("wind_direction"), 0)
    context = {"wind_speed": wind, "wave_height": wave, "wind_direction": wind_direction, "tide_phase": prediction.get("tidePhase") or "", "tide_type": prediction.get("tideType") or ""}

    meta = _build_live_meta(racers, profiles, exhibit, original, exrank, wind, wave) if racers and len(racers) == 6 else {}
    review = _review_deltas(racers, exhibit, direct, meta)
    adjusted = {pos: {str(lane): num(baseline[pos][str(lane)]) + review[str(lane)][pos] for lane in LANES} for pos in ("win", "second", "third")}
    collapse, penalty, attacks = _inside_breakdown(adjusted, meta)
    scenarios, _ = _live_scenarios(baseline.get("scenarios") or prediction.get("scenarios") or [], racers, profiles, base_scores, context, meta, collapse)
    for pos in adjusted:
        adjusted[pos] = normalize_map(adjusted[pos])
    prediction["win"] = adjusted["win"]

    residual_by_head = _residual_boosts(meta, scenarios)
    prediction["secondByHead"] = _adjust_conditionals(baseline.get("secondByHead"), review, "second", residual_by_head)
    prediction["thirdByHead"] = _adjust_conditionals(baseline.get("thirdByHead"), review, "third", residual_by_head)
    prediction["second"] = marginal_second(prediction["win"], prediction["secondByHead"])
    prediction["third"] = marginal_third(prediction["win"], prediction["secondByHead"], prediction["thirdByHead"])
    prediction["scenarios"] = scenarios

    sab, axis, gap = judge_sab(prediction["win"], scenarios, prediction["secondByHead"], prediction["thirdByHead"])
    prediction["sab"] = sab
    prediction["confidence"] = round(clamp(47 + gap * 2 + (8 if sab == "S" else 3 if sab == "A" else 0), 40, 88))
    prediction["readability"] = {"axisLane": axis, "comment": f"主軸{axis}号艇／直前・合算・回り足・決まり手連動再計算後"}
    prediction["ai"] = build_tickets(prediction["win"], prediction["secondByHead"], prediction["thirdByHead"], scenarios, sab)
    prediction["aiUpset"] = build_upset_tickets(prediction["win"], prediction["secondByHead"], prediction["thirdByHead"], scenarios)
    prediction["tickets"] = [x["combo"] for x in prediction["ai"]]

    prediction["probabilityReview"] = {}
    for k in map(str, LANES):
        prediction["probabilityReview"][k] = {
            "morningWin": baseline["win"][k], "morningSecond": baseline["second"][k], "morningThird": baseline["third"][k],
            "win": prediction["win"][k], "second": prediction["second"][k], "third": prediction["third"][k],
            "deltaWin": round(prediction["win"][k] - baseline["win"][k], 1), "deltaSecond": round(prediction["second"][k] - baseline["second"][k], 1), "deltaThird": round(prediction["third"][k] - baseline["third"][k], 1),
        }
    prediction["insideBreakdown"] = {"score": round(collapse, 3), "winPenaltyPoints": round(penalty, 2), "lane1Defense": round(meta.get("1", {}).get("defenseScore", 0), 3), "attackers": [{"lane": lane, "headScore": round(score, 3)} for lane, score in attacks]}
    prediction["entryChangedDetected"] = bool(entry_changed)
    prediction["actualCourseMap"] = {str(k): v for k, v in course_map.items()}
    prediction["liveAttackMeta"] = meta
    prediction["probabilityReviewStatus"] = "reviewed"
    prediction.setdefault("probabilityFlow", {}).update({"realtimeApplied": True, "reviewed": True, "reviewLabel": "確率補正・合算/回り足・決まり手マッチアップ・相手連動・SAB・買い目再計算済み"})
    prediction["predictionStage"] = {"label": "本予想", "statusText": "戸田v6：実進入・展示・合算・回り足・直線・決まり手・攻め連動再計算済み", "badge": "本予想", "color": "green"}
    prediction["liveReviewMeta"] = {"oddsUsedForProbability": False, "oddsRequiredForReview": False, "exhibitionStartUsedAlone": False, "originalExhibitionApplied": bool(original), "sumAndDifferenceApplied": bool(original), "turnApplied": bool(original), "straightApplied": bool(original), "kimariteMatchupApplied": bool(meta), "insideBreakdownApplied": bool(meta), "entryComparedDirectly": True, "entryChangedDetected": bool(entry_changed), "ticketsRegenerated": True, "headConditionalsRegenerated": True, "publicSecondThirdMarginalized": True}
    return True
