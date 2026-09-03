from __future__ import annotations

from itertools import permutations
from math import exp, isfinite
from typing import Any

ENGINE_ID = "fukuoka_engine_v1.0"
ENGINE_VERSION = "1.0"

BASE_WIN = {1: 53.35, 2: 14.48, 3: 14.98, 4: 8.70, 5: 4.99, 6: 3.50}
BASE_SECOND = {1: 20.0, 2: 23.5, 3: 21.5, 4: 16.0, 5: 11.0, 6: 8.0}
BASE_THIRD = {1: 15.0, 2: 20.0, 3: 21.0, 4: 19.0, 5: 15.0, 6: 10.0}
WIN_MIN_PERCENT = 0.1
WIN_DELTA_KEYS = (
    "course_delta", "local_delta", "motor_delta", "setsukan_delta",
    "water_delta", "slit_delta", "exhibition_delta", "original_delta",
    "interaction_delta",
)

HEAD_LINK = {
    3: {4: 1.20, 1: 1.15, 5: 1.10},
    4: {5: 1.20, 1: 1.15, 6: 1.05},
}
WEAK_FOUR_LINK = {5: 1.10, 6: 1.08, 1: 1.05}
OUTWARD_SECOND_LINK = {3: {4: 1.05, 5: 1.03}, 4: {5: 1.08, 6: 1.06}, 5: {6: 1.05}}
SLIT_OUTWARD_THIRD = {3: {4: 0.45, 5: 0.20}, 4: {5: 0.40, 6: 0.18}}

WIND_DIR_DELTA = {
    "N": 0.9, "NE": -1.1, "E": -1.6, "SE": 0.0,
    "S": -0.8, "SW": -2.7, "W": -0.2, "NW": 0.3, "CALM": 0.0,
}
SERIES_WEIGHTS = {
    1: (0.50, 0.20, 0.30),
    2: (0.40, 0.30, 0.30),
    3: (0.30, 0.40, 0.30),
    4: (0.25, 0.45, 0.30),
    5: (0.20, 0.50, 0.30),
    6: (0.15, 0.55, 0.30),
}


def num(v: Any, default: float = 0.0) -> float:
    if v in (None, "", "-"):
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if isfinite(x) else default


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def normalize(scores: dict[int, float]) -> dict[int, float]:
    clean = {k: max(1e-9, float(v)) for k, v in scores.items()}
    total = sum(clean.values())
    return {k: v / total for k, v in clean.items()}


def softmax(scores: dict[int, float], temperature: float) -> dict[int, float]:
    m = max(scores.values())
    ex = {k: exp((v - m) / max(temperature, 1e-6)) for k, v in scores.items()}
    return normalize(ex)


def rank_bonus(value: float | None, values: list[float], good_low: bool,
               best: float, upper: float, lower: float, worst: float) -> float:
    if value is None or len(values) < 4:
        return 0.0
    ordered = sorted(values, reverse=not good_low)
    pos = ordered.index(value) if value in ordered else len(ordered) // 2
    if pos == 0:
        return best
    if pos <= 1:
        return upper
    if pos >= len(ordered) - 1:
        return worst
    if pos >= len(ordered) - 2:
        return lower
    return 0.0


def escape_delta(rate: float) -> float:
    if rate < 10: return -9.0
    if rate < 20: return -7.5
    if rate < 25: return -6.0
    if rate < 37.5: return -4.0
    if rate < 50: return -2.0
    if rate < 60: return 0.0
    if rate < 72.73: return 2.0
    if rate < 85: return 4.0
    return 6.0


def lane2_sashi_delta(rate: float) -> float:
    if rate <= 0: return 0.0
    if rate < 10: return 0.5
    if rate < 15: return 1.0
    if rate < 25: return 2.0
    if rate < 35: return 3.5
    return 5.0


def lane3_attack_delta(makuri: float, makuri_sashi: float) -> float:
    if makuri <= 0: a = 0.0
    elif makuri < 10: a = 0.5
    elif makuri < 15: a = 1.0
    elif makuri < 25: a = 2.5
    elif makuri < 35: a = 4.0
    else: a = 5.5
    if makuri_sashi <= 0: b = 0.0
    elif makuri_sashi <= 5: b = 0.5
    elif makuri_sashi <= 10: b = 1.5
    elif makuri_sashi <= 20: b = 2.5
    else: b = 3.5
    return min(7.0, a + b)


def lane4_attack_delta(makuri: float, makuri_sashi: float) -> float:
    if makuri <= 0: a = 0.0
    elif makuri <= 5: a = 0.5
    elif makuri <= 10: a = 1.5
    elif makuri <= 20: a = 3.0
    else: a = 4.5
    if makuri_sashi <= 0: b = 0.0
    elif makuri_sashi <= 5: b = 0.5
    elif makuri_sashi <= 10: b = 1.5
    elif makuri_sashi <= 20: b = 2.5
    else: b = 3.5
    return min(6.0, a + b)


def motor_delta(grade: str | None, trend: str | None) -> float:
    d = {"S": 3.0, "A": 2.0, "B": 1.0, "C": 0.0, "D": -1.0, "E": -2.0}.get(str(grade or "C").upper(), 0.0)
    if trend == "up": d += 0.75
    elif trend == "down": d -= 0.75
    return clamp(d, -4.0, 4.0)


def setsukan_delta(runs: list[dict]) -> float:
    vals = []
    for r in runs or []:
        finish = str(r.get("finish", "")).replace("着", "")
        if finish.isdigit():
            vals.append({1: 2.0, 2: 1.1, 3: 0.5, 4: -0.2, 5: -0.8, 6: -1.2}.get(int(finish), 0.0))
    if not vals: return 0.0
    avg = sum(vals[-2:]) / len(vals[-2:])
    if avg >= 1.5: return 2.0
    if avg >= 0.5: return 1.0
    if avg <= -0.9: return -2.0
    if avg <= -0.3: return -1.0
    return 0.0


def local_delta(b: dict) -> float:
    local_win = num(b.get("local_win_rate"))
    nat_win = num(b.get("national_win_rate"))
    local_st = num(b.get("local_avg_st"), 0.18)
    d = clamp((local_win - nat_win) * 0.60, -1.8, 1.8)
    if local_st <= 0.14: d += 0.7
    elif local_st >= 0.20: d -= 0.5
    return clamp(d, -2.5, 2.5)


def wind_speed_delta(speed: float) -> float:
    if speed < 1: return 0.0
    if speed < 2: return 2.5
    if speed < 3: return 0.6
    if speed < 4: return -0.7
    if speed < 5: return -1.6
    if speed < 6: return -0.1
    return 0.0


def wave_delta(wave: float) -> float:
    if wave <= 2: return 1.1
    if wave <= 3: return -0.7
    return -1.1


def slit_structure(by_lane: dict[int, dict], by_course: dict[int, int]) -> dict:
    starts = {
        course: num(by_lane[lane].get("exhibition_st"), 9.0)
        for course, lane in by_course.items()
        if by_lane[lane].get("exhibition_st") not in (None, "", "-")
    }
    if len(starts) < 4:
        return {"peeking": [], "dented": [], "walls": [], "attackers": [], "outward": {}}

    ordered = sorted(starts.values())
    peeking = {
        course for course, start in starts.items()
        if ordered.index(start) <= 1
        and course > 1
        and starts.get(course - 1, start) - start >= 0.03
    }
    dented = {
        course for course, start in starts.items()
        if min(
            [starts.get(course - 1, start), starts.get(course + 1, start)]
        ) + 0.08 <= start
    }
    walls = set()
    for course in (2, 3):
        outer_start = starts.get(course + 1)
        own_start = starts.get(course)
        if own_start is not None and outer_start is not None and course not in dented:
            if own_start <= outer_start + 0.03:
                walls.add(course)

    attackers = []
    outward: dict[int, list[int]] = {}
    for course in (3, 4):
        lane = by_course.get(course)
        if lane is None or course not in peeking:
            continue
        boat = by_lane[lane]
        aptitude = (
            lane3_attack_delta(num(boat.get("course_makuri_rate")), num(boat.get("course_makuri_sashi_rate")))
            if course == 3
            else lane4_attack_delta(num(boat.get("course_makuri_rate")), num(boat.get("course_makuri_sashi_rate")))
        )
        if aptitude < 1.5:
            continue
        inside_course = course - 1
        if inside_course in walls and inside_course not in dented:
            continue
        attackers.append(course)
        outward[course] = [target for target in SLIT_OUTWARD_THIRD[course] if target in by_course]
    return {
        "peeking": sorted(peeking),
        "dented": sorted(dented),
        "walls": sorted(walls),
        "attackers": attackers,
        "outward": outward,
    }


class FukuokaPredictionEngineV10:
    """福岡 v1.0。オッズ・結果非使用の決定論的ルールエンジン。"""

    def predict(self, race: dict, debug: bool = False) -> dict:
        boats = sorted(race["boats"], key=lambda b: int(b["lane"]))
        if len(boats) != 6:
            raise ValueError("fukuoka_engine_requires_6_boats")

        by_lane = {int(b["lane"]): dict(b) for b in boats}
        by_course: dict[int, int] = {}
        for lane, b in by_lane.items():
            course = int(num(b.get("actual_course"), lane))
            if course not in range(1, 7) or course in by_course:
                course = lane
            b["actual_course"] = course
            by_course[course] = lane

        base_win = {l: BASE_WIN[b["actual_course"]] for l, b in by_lane.items()}
        win_deltas = {
            lane: {key: 0.0 for key in WIN_DELTA_KEYS}
            for lane in by_lane
        }

        def add_win_delta(lane: int, key: str, value: float) -> None:
            win_deltas[lane][key] += value

        sec_pts = {l: BASE_SECOND[b["actual_course"]] for l, b in by_lane.items()}
        thr_pts = {l: BASE_THIRD[b["actual_course"]] for l, b in by_lane.items()}

        c1 = by_course.get(1)
        if c1:
            add_win_delta(c1, "course_delta", escape_delta(num(by_lane[c1].get("course_escape_rate"), 53.35)))

        attack3 = attack4 = 0.0
        for lane, b in by_lane.items():
            c = b["actual_course"]
            if c == 2:
                add_win_delta(lane, "course_delta", lane2_sashi_delta(num(b.get("course_sashi_rate"))))
            elif c == 3:
                attack3 = lane3_attack_delta(num(b.get("course_makuri_rate")), num(b.get("course_makuri_sashi_rate")))
                add_win_delta(lane, "course_delta", attack3)
            elif c == 4:
                attack4 = lane4_attack_delta(num(b.get("course_makuri_rate")), num(b.get("course_makuri_sashi_rate")))
                add_win_delta(lane, "course_delta", attack4)

        if c1:
            er = num(by_lane[c1].get("course_escape_rate"), 53.35)
            c3, c4, c5 = by_course.get(3), by_course.get(4), by_course.get(5)
            if er < 37.5 and attack3 >= 2.5 and c3:
                add_win_delta(c1, "interaction_delta", -1.0)
                add_win_delta(c3, "interaction_delta", 1.0)
                if c4: thr_pts[c4] += 1.0
                if c5: thr_pts[c5] += 0.5
                if attack3 >= 5.0:
                    add_win_delta(c1, "interaction_delta", -2.0)
                    add_win_delta(c3, "interaction_delta", 1.5)
            if er < 37.5 and attack4 >= 2.5 and c4:
                add_win_delta(c1, "interaction_delta", -1.0)
                add_win_delta(c4, "interaction_delta", 1.0)
                if c5: thr_pts[c5] += 1.0
                if attack4 >= 4.5:
                    add_win_delta(c1, "interaction_delta", -1.0)
                    add_win_delta(c4, "interaction_delta", 1.0)

            c2 = by_course.get(2)
            d2 = lane2_sashi_delta(num(by_lane[c2].get("course_sashi_rate"))) if c2 else 0.0
            if d2 < 1.0 and attack3 < 1.5 and attack4 < 1.5 and er < 50:
                add_win_delta(c1, "interaction_delta", abs(escape_delta(er)) * 0.5)

        day = int(num(race.get("event_day"), 1))
        pre_w, set_w, live_w = SERIES_WEIGHTS.get(min(max(day, 1), 6), SERIES_WEIGHTS[1])
        for lane, b in by_lane.items():
            local = local_delta(b)
            motor = motor_delta(b.get("motor_grade"), b.get("motor_trend"))
            sd = setsukan_delta(b.get("setsukan_runs") or [])
            add_win_delta(lane, "local_delta", local * pre_w)
            add_win_delta(lane, "motor_delta", motor * pre_w)
            add_win_delta(lane, "setsukan_delta", sd * set_w)
            pre = local + motor
            sec_pts[lane] += pre * 0.45 * pre_w + sd * 0.65 * set_w
            thr_pts[lane] += pre * 0.25 * pre_w + sd * 0.45 * set_w

        if c1:
            water = WIND_DIR_DELTA.get(str(race.get("wind_direction") or "").upper(), 0.0)
            water += wind_speed_delta(num(race.get("wind_speed")))
            water += 0.5 * wave_delta(num(race.get("wave_height")))
            tide = str(race.get("tide_phase") or "").lower()
            water += 0.9 if tide == "rising" else (-0.7 if tide == "falling" else 0.0)
            add_win_delta(c1, "water_delta", clamp(water, -3.5, 3.5))

        ex = [num(b.get("exhibition_time"), 99) for b in by_lane.values() if b.get("exhibition_time") not in (None, "", "-")]
        laps = [num(b.get("original_lap"), 99) for b in by_lane.values() if b.get("original_lap") not in (None, "", "-")]
        turns = [num(b.get("original_turn"), 99) for b in by_lane.values() if b.get("original_turn") not in (None, "", "-")]
        straights = [num(b.get("original_straight"), 99) for b in by_lane.values() if b.get("original_straight") not in (None, "", "-")]
        sums = [num(b.get("original_sum"), 99) for b in by_lane.values() if b.get("original_sum") not in (None, "", "-")]
        sts = [num(b.get("exhibition_st"), 9) for b in by_lane.values() if b.get("exhibition_st") not in (None, "", "-")]
        slit = slit_structure(by_lane, by_course)

        for lane, b in by_lane.items():
            et = None if b.get("exhibition_time") in (None, "", "-") else num(b.get("exhibition_time"))
            lap = None if b.get("original_lap") in (None, "", "-") else num(b.get("original_lap"))
            turn = None if b.get("original_turn") in (None, "", "-") else num(b.get("original_turn"))
            straight = None if b.get("original_straight") in (None, "", "-") else num(b.get("original_straight"))
            original_sum = None if b.get("original_sum") in (None, "", "-") else num(b.get("original_sum"))
            st = None if b.get("exhibition_st") in (None, "", "-") else num(b.get("exhibition_st"))

            exhibition_component = rank_bonus(et, ex, True, 1.0, 0.5, -0.5, -1.0)
            original_component = 0.35 * rank_bonus(lap, laps, True, 1.5, 0.8, -0.5, -1.2)
            original_component += 0.35 * rank_bonus(turn, turns, True, 1.5, 0.8, -0.5, -1.5)
            original_component += 0.30 * rank_bonus(straight, straights, True, 1.5, 0.8, -0.4, -1.0)
            original_component += 0.15 * rank_bonus(original_sum, sums, True, 0.6, 0.3, -0.2, -0.5)
            slit_component = 0.0

            course = b["actual_course"]
            if course in slit["attackers"]:
                aptitude = attack3 if course == 3 else attack4
                slit_component += min(0.55, aptitude * 0.12)
            if course in slit["dented"]:
                slit_component -= 0.35

            if st is not None and len(sts) >= 4:
                rank = sorted(sts).index(st)
                local_st = num(b.get("local_avg_st"), 0.18)
                strong_local = num(b.get("local_win_rate")) >= max(5.5, num(b.get("national_win_rate")))
                if rank <= 1:
                    slit_component += 0.5
                elif rank >= 4:
                    if strong_local and local_st <= 0.16: slit_component -= 0.25
                    elif local_st <= 0.16: slit_component -= 0.6
                    else: slit_component -= 1.0

            live_unclamped = exhibition_component + original_component + slit_component
            live = clamp(live_unclamped, -2.5, 2.5)
            live_scale = live / live_unclamped if live_unclamped else 1.0
            add_win_delta(lane, "exhibition_delta", exhibition_component * live_scale * live_w)
            add_win_delta(lane, "original_delta", original_component * live_scale * live_w)
            add_win_delta(lane, "slit_delta", slit_component * live_scale * live_w)
            sec_pts[lane] += live * 0.85 * live_w

            c = b["actual_course"]
            thr_pts[lane] += {3: 1.5, 4: 1.0, 5: 0.5, 6: 0.25}.get(c, 0.0)
            if st is not None and len(sts) >= 4:
                sr = sorted(sts).index(st)
                if sr <= 1: thr_pts[lane] += 1.5
                elif sr <= 2: thr_pts[lane] += 1.0
                elif sr >= 4: thr_pts[lane] -= 0.5
            if turn is not None and len(turns) >= 4:
                tr = sorted(turns).index(turn)
                if tr <= 1: thr_pts[lane] += 1.5
                elif tr <= 3: thr_pts[lane] += 0.5
                elif tr == len(turns) - 1: thr_pts[lane] -= 1.5

        for attack_course in slit["attackers"]:
            for target_course, bonus in SLIT_OUTWARD_THIRD[attack_course].items():
                target_lane = by_course.get(target_course)
                if target_lane is not None:
                    thr_pts[target_lane] += bonus

        live_applied = any((ex, laps, turns, straights, sums, sts))
        delta_limit = 13.0 if live_applied else 10.0
        uncapped_total_deltas = {
            lane: sum(win_deltas[lane].values())
            for lane in by_lane
        }
        total_deltas = {
            lane: clamp(total, -delta_limit, delta_limit)
            for lane, total in uncapped_total_deltas.items()
        }
        raw_win = {
            lane: max(WIN_MIN_PERCENT, base_win[lane] + total_deltas[lane])
            for lane in by_lane
        }
        win = normalize(raw_win)

        c2 = by_course.get(2)
        floor_status = {"activated": False, "target": None, "achieved": None}
        if c1 and c2:
            b2 = by_lane[c2]
            st2, et2 = b2.get("exhibition_st"), b2.get("exhibition_time")
            st_rank = sorted(sts).index(num(st2)) if st2 not in (None, "", "-") and len(sts) >= 4 else 99
            exhibition_rank = sorted(ex).index(num(et2)) if et2 not in (None, "", "-") and ex else 0
            st_top = st_rank <= 1
            original_ranks = []
            for key, values in (("original_lap", laps), ("original_turn", turns), ("original_straight", straights)):
                value = b2.get(key)
                if value not in (None, "", "-") and values:
                    original_ranks.append(sorted(values).index(num(value)))
            original_mid = not original_ranks or sum(rank <= 3 for rank in original_ranks) >= 2
            foot_mid = (et2 in (None, "", "-") or not ex or exhibition_rank <= 3) and original_mid
            er1 = num(by_lane[c1].get("course_escape_rate"), 53.35)
            if st_top and foot_mid and er1 < 72.73:
                d2 = lane2_sashi_delta(num(b2.get("course_sashi_rate")))
                target = clamp(
                    0.20 + (0.005 if st_rank == 0 else 0.0) + min(0.005, d2 * 0.001),
                    0.20,
                    0.21,
                )
                if win[c2] < target:
                    other_raw = sum(value for lane, value in raw_win.items() if lane != c2)
                    required_raw = target * other_raw / (1.0 - target)
                    needed = max(0.0, required_raw - raw_win[c2])
                    available = max(0.0, delta_limit - total_deltas[c2])
                    floor_delta = min(needed, available)
                    win_deltas[c2]["interaction_delta"] += floor_delta
                    uncapped_total_deltas[c2] += floor_delta
                    total_deltas[c2] += floor_delta
                    raw_win[c2] = max(WIN_MIN_PERCENT, base_win[c2] + total_deltas[c2])
                    win = normalize(raw_win)
                floor_status = {
                    "activated": True,
                    "target": round(target, 4),
                    "achieved": round(win[c2], 4),
                }

        second = softmax(sec_pts, 8.0)
        third = softmax(thr_pts, 7.5)
        tickets = self._tickets(by_lane, by_course, win, second, third)
        fit = self._fit(by_lane, race, win)
        grade = "S" if fit >= 85 else ("A" if fit >= 70 else ("B" if fit >= 55 else "C"))

        diagnostics = {
            "odds_used": False,
            "result_used": False,
            "conditional_ticket_model": True,
            "win_normalization": "linear_percent_points",
            "win_delta_limit": delta_limit,
            "weak_four_linkage": {"5": 1.10, "6": 1.08, "1": 1.05, "protected_max": 1},
            "motor_adjustment_bounds": [-4.0, 4.0],
            "slit_structure": slit,
            "lane2_floor": floor_status,
            "original_sum_used": bool(sums),
            "protected_four_ticket": tickets.get("protected_four_ticket"),
        }
        if debug:
            diagnostics["win_audit"] = [{
                "lane": lane,
                "actual_course": by_lane[lane]["actual_course"],
                "base_win": round(base_win[lane], 6),
                **{key: round(win_deltas[lane][key], 6) for key in WIN_DELTA_KEYS},
                "uncapped_total_delta": round(uncapped_total_deltas[lane], 6),
                "total_delta": round(total_deltas[lane], 6),
                "raw_win": round(raw_win[lane], 6),
                "normalized_win": round(win[lane] * 100.0, 6),
            } for lane in range(1, 7)]

        return {
            "engine": ENGINE_ID,
            "engine_version": ENGINE_VERSION,
            "boats": [{
                "lane": lane,
                "actual_course": by_lane[lane]["actual_course"],
                "win_prob": round(win[lane], 6),
                "second_prob": round(second[lane], 6),
                "third_prob": round(third[lane], 6),
            } for lane in range(1, 7)],
            "tickets": tickets,
            "sab": {"grade": grade, "fit": round(fit, 1)},
            "diagnostics": diagnostics,
        }

    def _second_given_head(self, h, second, by_lane, by_course):
        weights = {l: (second[l] if l != h else 0.0) for l in range(1, 7)}
        for target_course, mul in HEAD_LINK.get(by_lane[h]["actual_course"], {}).items():
            lane = by_course.get(target_course)
            if lane and lane != h: weights[lane] *= mul
        return normalize(weights)

    def _third_given_pair(self, h, s, third, by_lane, by_course):
        weights = {l: (third[l] if l not in (h, s) else 0.0) for l in range(1, 7)}
        hcourse = by_lane[h]["actual_course"]
        for target_course, mul in HEAD_LINK.get(hcourse, {}).items():
            lane = by_course.get(target_course)
            if lane and lane not in (h, s): weights[lane] *= mul

        if hcourse == 4 or by_lane[s]["actual_course"] == 4:
            for target_course, mul in WEAK_FOUR_LINK.items():
                lane = by_course.get(target_course)
                if lane and lane not in (h, s): weights[lane] *= mul

        for target_course, mul in OUTWARD_SECOND_LINK.get(by_lane[s]["actual_course"], {}).items():
            lane = by_course.get(target_course)
            if lane and lane not in (h, s): weights[lane] *= mul
        return normalize(weights)

    def _tickets(self, by_lane, by_course, win, second, third):
        scored = []
        for h, s, t in permutations(range(1, 7), 3):
            p2 = self._second_given_head(h, second, by_lane, by_course)[s]
            p3 = self._third_given_pair(h, s, third, by_lane, by_course)[t]
            scored.append((win[h] * p2 * p3, h, s, t))
        scored.sort(reverse=True)

        # Main/Zure/Ana は条件付き総合順位の上位10点を確定してから役割を割り当てる。
        selected = list(scored[:10])
        seen = {item[1:] for item in selected}

        # 4連動は最大1点のみ保護。2/3を下げて押し出す設計にはしない。
        four = [
            x for x in scored
            if (by_lane[x[1]]["actual_course"] == 4 or by_lane[x[2]]["actual_course"] == 4)
            and by_lane[x[3]]["actual_course"] in (1, 5, 6)
        ]
        protected_four_ticket = None
        if four and selected:
            best4 = four[0]
            if best4[0] >= selected[-1][0] * 0.72:
                combo = f"{best4[1]}-{best4[2]}-{best4[3]}"
                if best4[1:] not in seen:
                    selected[-1] = best4
                    protected_four_ticket = combo

        selected = sorted(selected[:10], reverse=True)
        rows = []
        for i, (score, h, s, t) in enumerate(selected):
            role = "main" if i < 6 else ("deviation" if i < 8 else "upset")
            rows.append({"ticket": f"{h}-{s}-{t}", "score": round(score, 8), "role": role})
        return {
            "main": [r["ticket"] for r in rows[:6]],
            "deviation": [r["ticket"] for r in rows[6:8]],
            "upset": [r["ticket"] for r in rows[8:10]],
            "ranked_top10": rows,
            "protected_four_ticket": protected_four_ticket,
            "four_protection_candidates": [f"{x[1]}-{x[2]}-{x[3]}" for x in four],
        }

    def _fit(self, by_lane, race, win):
        ordered = sorted(win.values(), reverse=True)
        gap = (ordered[0] - ordered[1]) * 100
        score = 64.0 + min(12.0, gap * 0.65)
        if all(b.get("actual_course") for b in by_lane.values()): score += 5
        if all(b.get("exhibition_time") not in (None, "", "-") for b in by_lane.values()): score += 4
        if all(b.get("original_turn") not in (None, "", "-") for b in by_lane.values()): score += 5
        if num(race.get("wind_speed")) <= 5: score += 3
        return clamp(score, 0.0, 100.0)
