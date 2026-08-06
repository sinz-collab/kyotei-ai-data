from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import math
from .utils import clamp, normalize, safe_float, softmax
from .master_db import MasterDB

GRADE_SCORE = {"A1": 1.0, "A2": 0.55, "B1": 0.0, "B2": -0.45}
COURSE_PRIOR = {
    1: (0.45, 0.20, 0.11),
    2: (0.20, 0.31, 0.24),
    3: (0.13, 0.27, 0.25),
    4: (0.09, 0.20, 0.27),
    5: (0.05, 0.14, 0.23),
    6: (0.02, 0.08, 0.18),
}

@dataclass
class BoatEvaluation:
    lane: int
    entry_course: int
    win_logit: float
    second_logit: float
    third_logit: float
    signals: list[str]
    risks: list[str]
    support_count: int

class ProbabilityEngine:
    def __init__(self, master: MasterDB, config: dict[str, Any]):
        self.master = master
        self.config = config

    @staticmethod
    def _day_weights(day_no: int) -> tuple[float, float, float]:
        table = {
            1: (0.45, 0.40, 0.15),
            2: (0.35, 0.35, 0.30),
            3: (0.25, 0.30, 0.45),
            4: (0.20, 0.25, 0.55),
            5: (0.15, 0.25, 0.60),
        }
        if day_no >= 6:
            return (0.10, 0.20, 0.70)
        return table.get(max(1, day_no), table[1])

    @staticmethod
    def _rate(value: Any) -> float:
        x = safe_float(value)
        return x / 100.0 if x > 1 else x

    def _reliability_weight(self, row: dict) -> float:
        rel = str(row.get("reliability", "C"))
        return self.config["reliability"].get(rel, 0.35)

    def evaluate_boat(self, boat: dict, race: dict, water: dict) -> BoatEvaluation:
        lane = int(boat["lane"])
        course = int(boat.get("entry_course") or lane)
        prior = COURSE_PRIOR[course]
        win = math.log(max(prior[0], .002))
        second = math.log(max(prior[1], .002))
        third = math.log(max(prior[2], .002))
        signals, risks = [], []
        support = 0

        grade = str(boat.get("grade", "B1"))
        grade_score = GRADE_SCORE.get(grade, 0.0)
        win += 0.33 * grade_score
        second += 0.18 * grade_score
        third += 0.10 * grade_score
        if grade in ("A1", "A2"):
            signals.append("grade_A1_or_A2")
            support += 1

        national = safe_float(boat.get("national_win_rate"), 5.0)
        local = safe_float(boat.get("local_win_rate"), national)
        ability = clamp((0.55 * national + 0.45 * local - 5.0) / 2.5, -1.2, 1.2)
        win += 0.42 * ability
        second += 0.26 * ability
        third += 0.16 * ability
        if local >= national and local >= 5.5:
            signals.append("local_win_rate_at_least_national")
            support += 1
        elif local + 0.4 < national:
            risks.append("local_performance_below_national")
            if lane == 1:
                win -= 0.16

        player_id = str(boat.get("player_id", "")).zfill(4)
        pc = self.master.player_course(player_id, course)
        if pc:
            w = self._reliability_weight(pc)
            baseline = self._rate(pc.get("course_avg_top3_rate"))
            top3 = self._rate(pc.get("top3_rate"))
            win_rate = self._rate(pc.get("win_rate"))
            delta = clamp(top3 - baseline, -0.25, 0.25)
            win += w * (0.9 * delta + 0.55 * (win_rate - prior[0]))
            second += w * 0.55 * delta
            third += w * 0.40 * delta
            if delta > 0.04 and w >= 0.7:
                signals.append("player_course_reliable_above_baseline")
                support += 1
            elif delta < -0.06:
                risks.append("player_course_below_baseline")

        lane_row = self.master.player_lane(player_id, lane)
        if lane_row:
            w = self._reliability_weight(lane_row)
            lane_win = self._rate(lane_row.get("win_rate"))
            win += w * 0.40 * clamp(lane_win - prior[0], -0.25, 0.25)

        kim = boat.get("kimarite", {}) or {}
        escape = self._rate(kim.get("nige_rate"))
        escaped_against = self._rate(kim.get("escaped_against_rate"))
        attacked_against = self._rate(kim.get("attacked_against_rate"))
        if lane == 1:
            if escape >= 0.58:
                win += 0.12
                signals.append("escape_rate_good")
                support += 1
            elif escape and escape < 0.45:
                win -= 0.18
                second += 0.07
                risks.append("escape_rate_weak")
            if escaped_against > 0.22 or attacked_against > 0.22:
                win -= 0.12
                second += 0.04
                risks.append("lane1_attack_vulnerability")
        else:
            attack = max(
                self._rate(kim.get("sashi_rate")),
                self._rate(kim.get("makuri_rate")),
                self._rate(kim.get("makurizashi_rate")),
            )
            if attack >= 0.20:
                win += 0.11
                signals.append("attacker_strength")

        day_no = int(race.get("day_no", 1))
        motor_w, exhibition_w, meeting_w = self._day_weights(day_no)

        motor = boat.get("motor_recent10", {}) or {}
        motor_form = clamp(safe_float(motor.get("form_score")), -1, 1)
        straight = clamp(safe_float(motor.get("straight_score")), -1, 1)
        turn = clamp(safe_float(motor.get("turn_score")), -1, 1)
        motor_score = clamp(0.55 * motor_form + 0.225 * straight + 0.225 * turn, -1, 1)

        mrow = self.master.motor_course(int(boat.get("motor_no", 0)), course)
        if mrow and safe_float(mrow.get("starts")) >= 30:
            mwin = self._rate(mrow.get("win_rate"))
            motor_score += 0.20 * clamp(mwin - prior[0], -0.20, 0.20)

        meeting = boat.get("meeting", {}) or {}
        meeting_score = clamp(safe_float(meeting.get("form_score")), -1, 1)

        ex = boat.get("exhibition", {}) or {}
        time_rank = int(ex.get("time_rank", 3) or 3)
        sum_rank = int(ex.get("original_sum_rank", 3) or 3)
        exhibition_score = 0.0
        exhibition_score += {1: 0.75, 2: 0.45, 3: 0.15, 4: -0.10, 5: -0.35, 6: -0.65}.get(time_rank, 0.0)
        exhibition_score += {1: 0.65, 2: 0.40, 3: 0.15, 4: -0.10, 5: -0.30, 6: -0.55}.get(sum_rank, 0.0)
        exhibition_score = clamp(exhibition_score / 1.40, -1, 1)

        form_score = motor_w * motor_score + exhibition_w * exhibition_score + meeting_w * meeting_score
        win += 0.22 * form_score
        second += 0.14 * form_score
        third += 0.10 * form_score

        if meeting_score >= 0.35:
            signals.append("meeting_form_positive")
            support += 1
        if exhibition_score >= 0.50:
            signals.append("exhibition_confirmation_positive")
        elif exhibition_score <= -0.50:
            risks.append("exhibition_weak")

        if int(race.get("race_no", 1)) <= 4:
            near_extreme = safe_float(water.get("minutes_from_nearest_extreme"), 999)
            tide_direction = water.get("tide_direction", "unknown")
            if lane == 1 and grade in ("B1", "B2") and (near_extreme <= 60 or tide_direction == "stop"):
                win -= 0.14
                second += 0.05
                risks.append("early_race_tide_lane1_risk")

        wind = safe_float(water.get("wind_speed_mps"))
        wave = safe_float(water.get("wave_height_cm"))
        if wind >= 5 or wave >= 5:
            win -= 0.035 if lane == 1 else 0.0
            third += 0.025 if lane >= 4 else 0.0

        return BoatEvaluation(lane, course, win, second, third, signals, risks, support)

    def _apply_lane1_cap(self, evaluations: list[BoatEvaluation], win_probs: list[float], boats: list[dict]) -> list[float]:
        lane1_index = next((i for i, e in enumerate(evaluations) if e.lane == 1), None)
        if lane1_index is None:
            return win_probs
        ev = evaluations[lane1_index]
        boat = boats[lane1_index]
        anti = self.config["anti_inside_bias"]
        cap = anti["venue_lane1_anchor_cap"]
        grade = str(boat.get("grade", "B1"))
        national = safe_float(boat.get("national_win_rate"), 5.0)
        local = safe_float(boat.get("local_win_rate"), national)
        if ev.support_count < anti["lane1_strong_support_required"]:
            cap = min(cap, anti["lane1_cap_without_local_grade_support"])
        if grade in ("B1", "B2") and local < max(5.2, national - 0.2):
            cap = min(cap, anti["lane1_cap_b1_b2_weak_local"])
        # Dynamic inside ceiling: cap is not a target. Risks and missing support
        # can lower the permitted lane-1 share below the generic ceiling.
        dynamic_cap = cap
        dynamic_cap -= min(0.12, 0.035 * len(ev.risks))
        if ev.support_count == 0:
            dynamic_cap -= 0.035
        dynamic_cap = max(0.24, dynamic_cap)
        if win_probs[lane1_index] <= dynamic_cap:
            return win_probs
        excess = win_probs[lane1_index] - dynamic_cap
        win_probs[lane1_index] = dynamic_cap
        others = [i for i in range(len(win_probs)) if i != lane1_index]
        denom = sum(win_probs[i] for i in others)
        if denom <= 0:
            for i in others:
                win_probs[i] += excess / len(others)
        else:
            for i in others:
                win_probs[i] += excess * win_probs[i] / denom
        return normalize(win_probs)

    def _relative_inside_transfer(
        self,
        evaluations: list[BoatEvaluation],
        boats: list[dict],
        win_probs: list[float],
    ) -> list[float]:
        """Transfer probability from lane 1 to lane 2-4 when relative evidence is strong."""
        by_lane = {int(b["lane"]): b for b in boats}
        idx = {e.lane: i for i, e in enumerate(evaluations)}
        if 1 not in idx:
            return win_probs

        lane1 = by_lane.get(1, {})
        grade_order = {"B2": 0, "B1": 1, "A2": 2, "A1": 3}
        g1 = grade_order.get(str(lane1.get("grade", "B1")), 1)
        local1 = safe_float(lane1.get("local_win_rate"), safe_float(lane1.get("national_win_rate"), 5.0))
        local3_1 = self._rate(lane1.get("local_3"))
        transfer_total = 0.0
        allocations: list[tuple[int, float]] = []

        for lane in (2, 3, 4):
            if lane not in idx or lane not in by_lane:
                continue
            boat = by_lane[lane]
            grade_gap = grade_order.get(str(boat.get("grade", "B1")), 1) - g1
            local = safe_float(boat.get("local_win_rate"), safe_float(boat.get("national_win_rate"), 5.0))
            local_gap = local - local1
            local3 = self._rate(boat.get("local_3"))
            local3_gap = local3 - local3_1

            ex = boat.get("exhibition", {}) or {}
            ex1 = lane1.get("exhibition", {}) or {}
            ex_adv = 0.0
            if int(ex.get("time_rank", 9) or 9) <= 2:
                ex_adv += 0.015
            if int(ex.get("original_sum_rank", 9) or 9) <= 2:
                ex_adv += 0.015
            if int(ex1.get("time_rank", 9) or 9) >= 4:
                ex_adv += 0.010

            score = 0.0
            if grade_gap >= 2:
                score += 0.025
            elif grade_gap == 1:
                score += 0.015

            if local_gap >= 1.5:
                score += 0.035
            elif local_gap >= 1.0:
                score += 0.025
            elif local_gap >= 0.5:
                score += 0.012

            if local3_gap >= 0.20:
                score += 0.018
            elif local3_gap >= 0.10:
                score += 0.010

            score += ex_adv
            score = min(0.055, score)

            if score > 0:
                allocations.append((lane, score))
                transfer_total += score

        transfer_total = min(0.075, transfer_total)
        if transfer_total <= 0:
            return win_probs

        lane1_i = idx[1]
        max_transfer = max(0.0, win_probs[lane1_i] - 0.32)
        actual_transfer = min(transfer_total, max_transfer)
        if actual_transfer <= 0:
            return win_probs

        alloc_sum = sum(score for _, score in allocations)
        win_probs[lane1_i] -= actual_transfer
        for lane, score in allocations:
            win_probs[idx[lane]] += actual_transfer * score / alloc_sum

        return normalize(win_probs)

    def calculate(self, boats: list[dict], race: dict, water: dict) -> dict:
        evaluations = [self.evaluate_boat(b, race, water) for b in boats]
        win = softmax([e.win_logit for e in evaluations])
        second = softmax([e.second_logit for e in evaluations])
        third = softmax([e.third_logit for e in evaluations])
        win = self._apply_lane1_cap(evaluations, win, boats)
        win = self._relative_inside_transfer(evaluations, boats, win)

        rows = []
        for i, e in enumerate(evaluations):
            rows.append({
                "lane": e.lane,
                "entry_course": e.entry_course,
                "win": win[i],
                "second": second[i],
                "third": third[i],
                "top3": min(1.0, win[i] + second[i] + third[i]),
                "signals": e.signals,
                "risks": e.risks,
                "lane1_support_count": e.support_count if e.lane == 1 else None,
            })
        return {"boats": rows, "win": win, "second": second, "third": third}
