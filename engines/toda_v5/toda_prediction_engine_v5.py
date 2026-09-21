from pathlib import Path
from toda_utils_v5 import LANES, num, clamp, exp_softmax
from toda_master_loader_v5 import TodaMasterV5
from toda_scenario_engine_v5 import apply_scenario_mix, detect_scenarios, inside_weakness
from toda_ticket_engine_v5 import (
    build_head_conditionals,
    build_tickets,
    build_upset_tickets,
    marginal_second,
    marginal_third,
)
from toda_sab_engine_v5 import judge_sab

ENGINE_ID = "toda_prediction_engine_v6_20260814_marginal_conditional_ticket"
MASTER_ID = "Toda_AI_MASTER_v3_1_COMPLETE_ONE_FILE"

CLASS_BONUS = {"A1": 2.25, "A2": 1.20, "B1": 0.0, "B2": -1.15}
LANE_PRIOR = {1: 1.65, 2: .50, 3: .22, 4: .03, 5: -.28, 6: -.58}
TODA_COURSE_AVG_WIN = {
    1: 44.813005666705216,
    2: 17.057057396928048,
    3: 16.374079676674366,
    4: 15.418262272412997,
    5: 7.683431542461006,
    6: 2.6901005847953217,
}
TODA_COURSE_AVG_TOP3 = {1: 73.55, 2: 58.56, 3: 54.29, 4: 51.44, 5: 41.0, 6: 25.64}
COURSE_PRIOR_STRENGTH = 8.0
COURSE_SCORE_MAX = 2.0
COURSE_SCORE_DELTA_SCALE = 14.0


def _course_performance(profile, course):
    """Shrink course results to Toda priors and return one non-duplicated score."""
    course = int(course)
    avg_win = TODA_COURSE_AVG_WIN[course]
    avg_top3 = TODA_COURSE_AVG_TOP3[course]
    starts = max(0.0, num(profile.get("starts"), 0))
    confidence = starts / (starts + COURSE_PRIOR_STRENGTH)
    actual_win = num(profile.get("win_rate"), avg_win)
    actual_top3 = num(profile.get("top3_rate"), avg_top3)
    shrunk_win = confidence * actual_win + (1 - confidence) * avg_win
    shrunk_top3 = confidence * actual_top3 + (1 - confidence) * avg_top3
    delta = .70 * (shrunk_win - avg_win) + .30 * (shrunk_top3 - avg_top3)
    return {
        "score": clamp(delta / COURSE_SCORE_DELTA_SCALE, -COURSE_SCORE_MAX, COURSE_SCORE_MAX),
        "confidence": confidence,
        "shrunkWin": shrunk_win,
        "shrunkTop3": shrunk_top3,
        "avgWin": avg_win,
        "avgTop3": avg_top3,
        "weightedDelta": delta,
    }


def _positive_or(value, fallback):
    v = num(value, fallback)
    return v if v > 0 else fallback


def _win_temperature(win_raw, scenarios, racers, profiles):
    ranked = sorted(LANES, key=lambda lane: win_raw[str(lane)], reverse=True)
    head = ranked[0]
    gap = win_raw[str(head)] - win_raw[str(ranked[1])]
    head_scenario = next((s for s in scenarios if int(s["head"]) == head), None)
    evidence = (head_scenario or {}).get("evidence") or {}
    evidence_count = num(evidence.get("evidenceCount"), 0)
    profile = profiles[str(head)]
    racer = next(r for r in racers if int(r["lane"]) == head)
    course_win = num(profile.get("win_rate"), 0)
    if head == 2:
        kimarite = num(racer.get("boaters_sashi_rate"), 0) + .35 * num(racer.get("boaters_makuri_rate"), 0) + .20 * num(racer.get("boaters_makuri_sashi_rate"), 0)
    else:
        kimarite = num(racer.get("boaters_makuri_rate"), 0) + num(racer.get("boaters_makuri_sashi_rate"), 0) + .25 * num(racer.get("boaters_sashi_rate"), 0)
    contradictions = int(course_win <= 0) + int(head != 1 and kimarite <= 0)
    head_count = len({int(s["head"]) for s in scenarios})
    if gap >= 4 and (head == 1 or evidence_count >= 4):
        return .58
    if head_count >= 3 or gap < 1 or contradictions >= 2:
        return .80
    if head_count >= 2 or gap < 2.5 or contradictions:
        return .73
    return .60 if gap >= 3 and evidence_count >= 3 else .70


class TodaPredictionEngineV5:
    def __init__(self, master_dir=None):
        self.master = TodaMasterV5(master_dir)

    def _season_form(self, r):
        runs = list(r.get("season_runs") or [])[-8:]
        if not runs:
            return 0
        total = weight = 0
        for i, x in enumerate(runs):
            w = 1 + i * .10
            finish = num(x.get("finish"), 6)
            st = num(x.get("st"), .19)
            course = num(x.get("course"), 3.5)
            total += w * ((4 - finish) * .52 + clamp((.19 - st) * 9, -.75, .75) + (.08 if course <= 3 else 0))
            weight += w
        return clamp(total / weight, -2.2, 2.2)

    def _base_score(self, r, lane, profile):
        nat = num(r.get("nat_win"), 4.5)
        local = _positive_or(r.get("local_win"), nat)
        avg = num(r.get("avg_st"), .18)
        local_st = num(r.get("local_st"), 9)
        course_st = num(profile.get("avg_st"), 9)
        motor = _positive_or(r.get("motor_2"), 32)
        boat = _positive_or(r.get("boat_2"), 32)
        course = int(r.get("actual_course") or r.get("entry_course") or lane)
        course_term = _course_performance(profile, course)["score"]
        st_term = clamp((.18 - avg) * 10, -.9, .9)
        if local_st < 1:
            st_term += clamp((.18 - local_st) * 7, -.65, .65)
        if course_st < 1:
            st_term += clamp((.18 - course_st) * 7, -.65, .65)
        return (
            CLASS_BONUS.get(str(r.get("class", "")), 0)
            + (nat - 4.5) * .88
            + (local - 4.5) * .70
            + (motor - 32) * .042
            + (boat - 32) * .016
            + st_term
            + course_term
            + LANE_PRIOR[lane]
            + self._season_form(r)
        )

    def predict(self, race, context=None):
        context = context or {}
        racers = list(race.get("racers") or [])
        if len(racers) != 6:
            raise ValueError("racers must contain six boats")
        profiles = {}
        source_status = {}
        logs = []
        scores = {}
        for r in racers:
            lane = int(r["lane"])
            course = int(r.get("actual_course") or r.get("entry_course") or lane)
            p = self.master.course_profile(r, course)
            profiles[str(lane)] = p
            scores[str(lane)] = self._base_score(r, lane, p)
            local_win_raw = num(r.get("local_win"), 0)
            motor_raw = num(r.get("motor_2"), 0)
            source_status[str(lane)] = {
                "player_course": "reflected" if p["matched"] else "missing",
                "sources": p.get("sources", []),
                "local_st": "reflected" if num(r.get("local_st"), 9) < 1 else "missing",
                "local_win": "reflected" if local_win_raw > 0 else "missing_neutral",
                "season": "reflected" if r.get("season_runs") else "missing",
                "motor": "reflected" if motor_raw > 0 else "missing_neutral",
            }
            logs.append({"lane": lane, "stage": "base", "notes": [
                f"course_profile={p['strength'] or 'none'}",
                f"course_sources={','.join(p.get('sources', [])) or 'none'}",
                f"local_win={'neutral_missing' if local_win_raw <= 0 else local_win_raw}",
                f"motor_2={'neutral_missing' if motor_raw <= 0 else motor_raw}",
                f"base_score={scores[str(lane)]:.3f}",
            ]})

        scenarios, one_weak = detect_scenarios(racers, profiles, scores, context)
        win_raw = dict(scores)
        for s in scenarios:
            win_raw[str(s["head"])] += s["weight"] * 1.28
        temperature = _win_temperature(win_raw, scenarios, racers, profiles)
        base_win = exp_softmax(scores, .25 / temperature)
        win, scenario_mix = apply_scenario_mix(base_win, scenarios)

        second_by_head = {}
        third_by_head = {}
        for h in LANES:
            s = next((x for x in scenarios if x["head"] == h), None)
            sec, thr = build_head_conditionals(h, scores, s)
            second_by_head[str(h)] = sec
            third_by_head[str(h)] = thr

        # Site-facing rates are race-wide marginals, not the SAB-axis conditional.
        second = marginal_second(win, second_by_head)
        third = marginal_third(win, second_by_head, third_by_head)
        sab, axis, gap = judge_sab(win, scenarios, second_by_head, third_by_head)
        tickets = build_tickets(win, second_by_head, third_by_head, scenarios, sab)
        upset = build_upset_tickets(win, second_by_head, third_by_head, scenarios)
        upset_index = round(clamp(100 - win["1"] + (12 if one_weak else 0), 5, 95), 1)
        tide_profile = self.master.tide_profile(context.get("tide_type"))
        source_summary = {
            "master": "reflected",
            "player_course_reflected": sum(1 for x in source_status.values() if x["player_course"] == "reflected"),
            "player_course_missing": sum(1 for x in source_status.values() if x["player_course"] == "missing"),
            "tide_summary": "reflected" if tide_profile else "missing",
            "odds_used_for_probability": False,
            "exhibition_st_used_alone": False,
            "public_second_third_are_marginals": True,
        }
        return {
            "engine": ENGINE_ID,
            "master": MASTER_ID,
            "tidePhase": context.get("tide_phase") or "",
            "tideType": context.get("tide_type") or "",
            "softmaxTemperature": temperature,
            "baseWin": base_win,
            "win": win,
            "second": second,
            "third": third,
            "secondByHead": second_by_head,
            "thirdByHead": third_by_head,
            "scenarios": scenarios,
            "oneWeak": one_weak,
            "insideWeakness": inside_weakness(
                next(r for r in racers if int(r["lane"]) == 1), profiles["1"]
            ),
            "scenarioMix": scenario_mix,
            "sab": sab,
            "confidence": round(clamp(47 + gap * 2 + (8 if sab == "S" else 3 if sab == "A" else 0), 40, 88)),
            "upsetIndex": upset_index,
            "attack": {
                "attackLane": next((s["head"] for s in scenarios if s["head"] != 1), axis),
                "label": next((s["label"] for s in scenarios if s["head"] != 1), f"{axis}号艇中心"),
            },
            "readability": {"axisLane": axis, "comment": f"主軸{axis}号艇／展開シナリオ連動"},
            "ai": tickets,
            "aiUpset": upset,
            "tickets": [x["combo"] for x in tickets],
            "sourceStatus": source_status,
            "sourceSummary": source_summary,
            "logs": logs,
            "modelInputs": {"racers": racers, "profiles": profiles, "baseScores": scores, "expectedEntry": [1, 2, 3, 4, 5, 6]},
            "probabilityFlow": {"required": True, "baseApplied": True, "baseLabel": "戸田v6事前基礎予想", "realtimeApplied": False, "reviewed": False, "reviewLabel": "直前情報待ち"},
            "predictionStage": {"label": "事前予想", "statusText": "戸田v6：統合マスター・選手×コース・決まり手展開連動反映", "badge": "事前", "color": "blue"},
        }
