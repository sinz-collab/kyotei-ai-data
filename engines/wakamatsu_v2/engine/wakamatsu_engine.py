
from __future__ import annotations

import argparse
import itertools
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


COURSE_PRIORS = {
    1: (0.549841, 0.176109, 0.092818),
    2: (0.126319, 0.237168, 0.193628),
    3: (0.122528, 0.214117, 0.199365),
    4: (0.106854, 0.176109, 0.190000),
    5: (0.059000, 0.115000, 0.175000),
    6: (0.036000, 0.081000, 0.149000),
}

RELIABILITY_WEIGHT = {"A": 0.72, "B": 0.52, "C": 0.30, "参考": 0.18, None: 0.20, "": 0.20}


def normalize(values: np.ndarray, total: float = 1.0) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    values = np.clip(values, 1e-8, None)
    return values / values.sum() * total


def pct(v: Any) -> float | None:
    if v is None or pd.isna(v):
        return None
    x = float(v)
    return x / 100.0 if abs(x) > 1.0 else x


def bounded_add(base: float, delta: float, cap: float) -> float:
    return max(1e-6, base + max(-cap, min(cap, delta)))


def time_band_from_race(race_no: int, start_time: str | None = None) -> str:
    if start_time:
        try:
            hour = datetime.strptime(start_time, "%H:%M").hour
            if hour < 16:
                return "昼/序盤"
        except ValueError:
            pass
    if race_no <= 4:
        return "ナイター前半"
    if race_no <= 9:
        return "ナイター中盤"
    return "ナイター後半"


@dataclass
class BoatProb:
    lane: int
    player_id: int | None
    entry_course: int
    win: float
    second: float
    third: float
    top3: float
    notes: list[str]


class WakamatsuEngine:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.con = sqlite3.connect(self.db_path)
        self.con.row_factory = sqlite3.Row
        self.course_base = pd.read_sql("SELECT * FROM course_baseline", self.con)
        self.water_adj = pd.read_sql("SELECT * FROM water_probability_adjustments", self.con)
        self.water_cond = pd.read_sql("SELECT * FROM water_type_conditions", self.con)

    def close(self) -> None:
        self.con.close()

    def _lookup_player_course(self, player_id: int | None, lane: int, entry_course: int) -> dict[str, Any] | None:
        if player_id is None:
            return None
        q = """
        SELECT * FROM player_entry_shift_stats
        WHERE CAST(player_id AS INTEGER)=? AND CAST(lane AS INTEGER)=? AND CAST(entry_course AS INTEGER)=?
        ORDER BY starts DESC LIMIT 1
        """
        row = self.con.execute(q, (int(player_id), int(lane), int(entry_course))).fetchone()
        if row:
            return dict(row)
        q = """
        SELECT * FROM player_course_stats
        WHERE CAST(player_id AS INTEGER)=? AND CAST(entry_course AS INTEGER)=?
        ORDER BY starts DESC LIMIT 1
        """
        row = self.con.execute(q, (int(player_id), int(entry_course))).fetchone()
        return dict(row) if row else None

    def _water_adjustment(self, tide_type: str | None, time_band: str, course: int) -> tuple[dict[str, float], dict[str, Any] | None]:
        if not tide_type:
            return {"win": 0.0, "second": 0.0, "third": 0.0}, None
        condition_key = f"{tide_type}×{time_band}"
        rows = self.water_adj[
            (self.water_adj["condition_key"].astype(str) == condition_key) &
            (self.water_adj["course"].astype(int) == int(course))
        ]
        if rows.empty:
            return {"win": 0.0, "second": 0.0, "third": 0.0}, None
        r = rows.iloc[0]
        info = self.water_cond[self.water_cond["condition_key"].astype(str) == condition_key]
        condition = None if info.empty else info.iloc[0].to_dict()
        if str(r.get("adopt_flag", "採用")) != "採用":
            return {"win": 0.0, "second": 0.0, "third": 0.0}, condition
        return {
            "win": float(r.get("corr_win", 0.0) or 0.0),
            "second": float(r.get("corr_second", 0.0) or 0.0),
            "third": float(r.get("corr_third", 0.0) or 0.0),
        }, condition

    @staticmethod
    def _event_stage_weights(event_day: int, race: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Separate motor, exhibition and meeting-form weights by event stage.
        race_stage may be one of: day1, day2, day3, day4, semifinal, final.
        """
        race = race or {}
        explicit = str(race.get("race_stage") or "").strip().lower()
        is_final = bool(race.get("is_final")) or explicit in {"final", "final_day", "最終日"}
        is_semifinal = bool(race.get("is_semifinal")) or explicit in {"semifinal", "semi", "準優", "準優日"}

        if is_final:
            stage = "final"
        elif is_semifinal:
            stage = "semifinal"
        elif event_day <= 1:
            stage = "day1"
        elif event_day == 2:
            stage = "day2"
        elif event_day == 3:
            stage = "day3"
        else:
            stage = "day4_plus"

        table = {
            "day1":      {"motor": 1.30, "exhibition": 0.90, "original": 0.90, "meeting": 0.00, "trend": 0.00},
            "day2":      {"motor": 1.15, "exhibition": 0.95, "original": 0.95, "meeting": 0.70, "trend": 0.65},
            "day3":      {"motor": 1.00, "exhibition": 1.00, "original": 1.00, "meeting": 1.00, "trend": 1.00},
            "day4_plus": {"motor": 0.90, "exhibition": 1.00, "original": 1.00, "meeting": 1.15, "trend": 1.15},
            "semifinal": {"motor": 0.85, "exhibition": 1.00, "original": 1.05, "meeting": 1.25, "trend": 1.25},
            "final":     {"motor": 0.80, "exhibition": 0.95, "original": 1.00, "meeting": 1.30, "trend": 1.30},
        }
        return {"stage": stage, **table[stage]}

    @staticmethod
    def _derive_meeting_form(boat: dict[str, Any]) -> dict[str, Any]:
        """
        Derive meeting level/trend/stability from chronological race records.
        Supported keys: meeting_runs or legacy season_runs.
        Each run may contain:
          day_no, race_no, finish, actual_course/entry_course/lane,
          start_time, exhibition_score, original_exhibition_score.
        """
        runs = boat.get("meeting_runs")
        if not isinstance(runs, list) or not runs:
            legacy = boat.get("season_runs")
            runs = legacy if isinstance(legacy, list) else []

        audit = {
            "available": False,
            "run_count": 0,
            "level_score": 0.0,
            "trend_score": 0.0,
            "recent_score": 0.0,
            "stability_score": 0.0,
            "trend_label": "no_data",
            "run_scores": [],
        }
        if not runs:
            # Backward-compatible externally supplied scalar.
            scalar = boat.get("meeting_score")
            if scalar is not None:
                level = max(-1.0, min(1.0, float(scalar)))
                audit.update({
                    "available": True,
                    "run_count": 1,
                    "level_score": level,
                    "recent_score": level,
                    "trend_label": "scalar_only",
                })
            return audit

        expected_finish = {1: 2.30, 2: 3.00, 3: 3.15, 4: 3.35, 5: 3.75, 6: 4.10}
        parsed = []
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                continue
            finish_raw = run.get("finish")
            if finish_raw in (None, "", "-", "F", "L", "K", "S"):
                continue
            try:
                finish = int(str(finish_raw).replace("着", "").strip())
            except Exception:
                continue
            if not 1 <= finish <= 6:
                continue

            course = int(run.get("actual_course") or run.get("entry_course") or run.get("lane") or 6)
            course = min(6, max(1, course))
            # Positive when result beats the course-adjusted expectation.
            result_component = max(-1.0, min(1.0, (expected_finish[course] - finish) / 2.4))

            st_component = 0.0
            st = run.get("start_time")
            if st not in (None, "", "-"):
                try:
                    st_component = max(-1.0, min(1.0, (0.18 - float(st)) / 0.08))
                except Exception:
                    pass

            ex_component = 0.0
            ex = run.get("exhibition_score")
            if ex not in (None, "", "-"):
                ex_component = max(-1.0, min(1.0, float(ex)))

            original_component = 0.0
            orig = run.get("original_exhibition_score")
            if orig not in (None, "", "-"):
                original_component = max(-1.0, min(1.0, float(orig)))

            score = (
                0.62 * result_component
                + 0.18 * st_component
                + 0.10 * ex_component
                + 0.10 * original_component
            )
            parsed.append({
                "index": index,
                "day_no": int(run.get("day_no") or 0),
                "race_no": int(run.get("race_no") or index + 1),
                "finish": finish,
                "course": course,
                "score": max(-1.0, min(1.0, score)),
            })

        if not parsed:
            return audit

        parsed.sort(key=lambda x: (x["day_no"], x["race_no"], x["index"]))
        scores = [x["score"] for x in parsed]
        n = len(scores)
        weights = [1.0 + 0.18 * i for i in range(n)]
        level = sum(s * w for s, w in zip(scores, weights)) / sum(weights)

        recent_n = min(3, n)
        recent = sum(scores[-recent_n:]) / recent_n
        if n >= 3:
            early_n = min(3, n - 1)
            early = sum(scores[:early_n]) / early_n
            trend = max(-1.0, min(1.0, (recent - early) / 0.85))
        elif n == 2:
            trend = max(-1.0, min(1.0, (scores[1] - scores[0]) / 0.75))
        else:
            trend = 0.0

        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / n
        stability = max(-1.0, min(1.0, 1.0 - (variance ** 0.5) / 0.60))

        if trend >= 0.22:
            label = "rising"
        elif trend <= -0.22:
            label = "falling"
        else:
            label = "flat"

        audit.update({
            "available": True,
            "run_count": n,
            "level_score": round(float(level), 6),
            "trend_score": round(float(trend), 6),
            "recent_score": round(float(recent), 6),
            "stability_score": round(float(stability), 6),
            "trend_label": label,
            "run_scores": [
                {
                    "day_no": x["day_no"],
                    "race_no": x["race_no"],
                    "finish": x["finish"],
                    "course": x["course"],
                    "score": round(float(x["score"]), 6),
                }
                for x in parsed
            ],
        })
        return audit

    def _base_probs(self, boat: dict[str, Any], time_band: str, tide_type: str | None) -> BoatProb:
        lane = int(boat["lane"])
        course = int(boat.get("entry_course") or lane)
        player_id = boat.get("player_id")
        pri = COURSE_PRIORS.get(course, COURSE_PRIORS[lane])
        win, second, third = pri
        notes = [f"course_prior:{course}"]

        stats = self._lookup_player_course(player_id, lane, course)
        if stats:
            starts = max(0, int(stats.get("starts") or 0))
            rel = str(stats.get("reliability") or "")
            shrink = RELIABILITY_WEIGHT.get(rel, 0.20) * min(1.0, starts / 30.0)
            pwin = pct(stats.get("win_rate"))
            psecond = pct(stats.get("second_rate"))
            pthird = pct(stats.get("third_rate"))
            if pwin is not None:
                win = win * (1 - shrink) + pwin * shrink
            if psecond is not None:
                second = second * (1 - shrink) + psecond * shrink
            if pthird is not None:
                third = third * (1 - shrink) + pthird * shrink
            notes.append(f"player_course:{rel or 'ref'}:{starts}")

            avg_st = stats.get("avg_st")
            if avg_st is not None and not pd.isna(avg_st):
                st_delta = (0.165 - float(avg_st)) * 0.22
                win = bounded_add(win, st_delta, 0.025)
                second = bounded_add(second, st_delta * 0.55, 0.015)
                notes.append("real_st_adjusted")

        w_adj, condition = self._water_adjustment(tide_type, time_band, course)
        win = bounded_add(win, w_adj["win"], 0.06)
        second = bounded_add(second, w_adj["second"], 0.07)
        third = bounded_add(third, w_adj["third"], 0.07)
        if condition:
            notes.append(f"water:{condition.get('water_type')}:{condition.get('confidence')}")

        # v1.3: local Wakamatsu performance and local actual ST.
        # Local figures are treated as independent probability corrections, not direct ranking fixes.
        event_day = int(boat.get("event_day") or 1)
        stage_weights = self._event_stage_weights(event_day, boat)
        local_win = boat.get("local_win")
        local_2 = boat.get("local_2")
        local_3 = boat.get("local_3")
        nat_win = boat.get("nat_win")
        nat_2 = boat.get("nat_2")
        nat_3 = boat.get("nat_3")
        local_st = boat.get("local_st")
        avg_st_input = boat.get("avg_st")

        # 0.00 local figures usually mean no valid local sample; do not penalize them.
        local_valid = local_win is not None and float(local_win) > 0.01
        if local_valid:
            lw = float(local_win)
            l2 = float(local_2 or 0.0)
            l3 = float(local_3 or 0.0)
            nw = float(nat_win or lw)
            n2 = float(nat_2 or l2)
            n3 = float(nat_3 or l3)

            # Blend absolute local level and local-vs-national differential.
            local_level = (
                ((lw - 5.20) / 1.70) * 0.45
                + ((l2 - 35.0) / 16.0) * 0.30
                + ((l3 - 52.0) / 18.0) * 0.25
            )
            local_diff = (
                ((lw - nw) / 1.40) * 0.45
                + ((l2 - n2) / 13.0) * 0.30
                + ((l3 - n3) / 15.0) * 0.25
            )
            local_score = max(-1.0, min(1.0, 0.62 * local_level + 0.38 * local_diff))

            # On day 1, local suitability is more valuable because meeting-form evidence is absent.
            local_day_weight = 1.18 if event_day == 1 else (1.05 if event_day == 2 else 0.92)
            win = bounded_add(win, 0.026 * local_score * local_day_weight, 0.036)
            second = bounded_add(second, 0.022 * local_score * local_day_weight, 0.030)
            third = bounded_add(third, 0.019 * local_score * local_day_weight, 0.026)
            notes.append(f"local_performance_day{event_day}")
        else:
            notes.append("local_performance_no_sample")

        if local_st is not None and str(local_st).strip() not in {"", "-", "None"}:
            lst = float(local_st)
            # Actual local ST is weighted above exhibition ST, but remains a bounded correction.
            st_score = max(-1.0, min(1.0, (0.175 - lst) / 0.045))
            if avg_st_input is not None and str(avg_st_input).strip() not in {"", "-", "None"}:
                ast = float(avg_st_input)
                st_score = max(-1.0, min(1.0, 0.72 * st_score + 0.28 * ((ast - lst) / 0.045)))
            win = bounded_add(win, 0.024 * st_score, 0.030)
            second = bounded_add(second, 0.017 * st_score, 0.022)
            third = bounded_add(third, 0.010 * st_score, 0.014)
            notes.append("local_actual_st")

        # Event-day dependent automatic motor/boat scoring.
        motor2 = boat.get("motor_2")
        motor3 = boat.get("motor_3")
        boat2 = boat.get("boat_2")
        boat3 = boat.get("boat_3")
        if motor2 is not None or motor3 is not None:
            m2 = float(motor2 or 35.0)
            m3 = float(motor3 or 52.0)
            raw = ((m2 - 35.0) / 12.0) * 0.58 + ((m3 - 52.0) / 16.0) * 0.42
            raw = max(-1.0, min(1.0, raw))
            motor_weight = float(stage_weights["motor"])
            win = bounded_add(win, 0.030 * raw * motor_weight, 0.045)
            second = bounded_add(second, 0.024 * raw * motor_weight, 0.035)
            third = bounded_add(third, 0.020 * raw * motor_weight, 0.030)
            notes.append(f"motor_stage:{stage_weights['stage']}:{motor_weight:.2f}")

        if boat2 is not None or boat3 is not None:
            b2 = float(boat2 or 35.0)
            b3 = float(boat3 or 52.0)
            raw = ((b2 - 35.0) / 14.0) * 0.55 + ((b3 - 52.0) / 17.0) * 0.45
            raw = max(-1.0, min(1.0, raw))
            win = bounded_add(win, 0.013 * raw, 0.018)
            second = bounded_add(second, 0.012 * raw, 0.016)
            third = bounded_add(third, 0.011 * raw, 0.015)
            notes.append("boat_auto")

        # v1.1: detailed low-tide/front-half correction.
        minutes_to_low = boat.get("minutes_to_low_tide")
        tide_phase = boat.get("tide_phase")
        if minutes_to_low is not None and tide_phase == "下げ潮":
            mins = abs(float(minutes_to_low))
            strength = max(0.0, min(1.0, (150.0 - mins) / 150.0))
            if course == 1:
                win = bounded_add(win, -0.040 * strength, 0.040)
                third = bounded_add(third, 0.018 * strength, 0.020)
                notes.append("low_tide_in_head_down_remain_up")
            elif course in (2, 3, 4):
                win = bounded_add(win, 0.018 * strength, 0.025)
                second = bounded_add(second, 0.014 * strength, 0.020)
                notes.append("low_tide_attack_up")

        # Optional race-day data; all are probability corrections, never hard fixes.
        motor = boat.get("motor_score")
        if motor is not None:
            m = max(-1.0, min(1.0, float(motor)))
            win = bounded_add(win, 0.022 * m, 0.03)
            second = bounded_add(second, 0.018 * m, 0.025)
            third = bounded_add(third, 0.015 * m, 0.02)
            notes.append("motor")

        meeting_form = self._derive_meeting_form(boat)
        if meeting_form["available"] and float(stage_weights["meeting"]) > 0.0:
            level = float(meeting_form["level_score"])
            trend = float(meeting_form["trend_score"])
            recent = float(meeting_form["recent_score"])
            stability = float(meeting_form["stability_score"])
            meeting_weight = float(stage_weights["meeting"])
            trend_weight = float(stage_weights["trend"])

            # Level and recent form build the base; trend controls rising/falling direction.
            combined_level = max(-1.0, min(1.0, 0.62 * level + 0.28 * recent + 0.10 * stability))
            combined_trend = max(-1.0, min(1.0, trend))

            win = bounded_add(
                win,
                (0.022 * combined_level * meeting_weight)
                + (0.018 * combined_trend * trend_weight),
                0.055,
            )
            second = bounded_add(
                second,
                (0.021 * combined_level * meeting_weight)
                + (0.016 * combined_trend * trend_weight),
                0.050,
            )
            third = bounded_add(
                third,
                (0.017 * combined_level * meeting_weight)
                + (0.010 * combined_trend * trend_weight),
                0.038,
            )
            notes.append(
                f"meeting_stage:{stage_weights['stage']}:{meeting_form['trend_label']}:"
                f"{meeting_weight:.2f}"
            )
        elif event_day <= 1:
            notes.append("meeting_day1_disabled")
        else:
            notes.append("meeting_no_data")

        exhibition = boat.get("exhibition_score")
        if exhibition is not None:
            e = max(-1.0, min(1.0, float(exhibition)))
            exhibition_weight = float(stage_weights["exhibition"])
            win = bounded_add(win, 0.018 * e * exhibition_weight, 0.025)
            second = bounded_add(second, 0.015 * e * exhibition_weight, 0.02)
            third = bounded_add(third, 0.014 * e * exhibition_weight, 0.02)
            notes.append(f"exhibition_stage:{stage_weights['stage']}:{exhibition_weight:.2f}")
            # v1.2: an inner-course mover is upgraded only when multiple signals agree.
            moved_in = lane - course
            if moved_in >= 1 and course in (2, 3) and e >= 0.45:
                win = bounded_add(win, 0.022 + 0.008 * min(2, moved_in), 0.038)
                second = bounded_add(second, 0.014, 0.018)
                notes.append("inner_move_multi_signal")

        original = boat.get("original_exhibition_score")
        if original is not None:
            o = max(-1.0, min(1.0, float(original)))
            original_weight = float(stage_weights["original"])
            win = bounded_add(win, 0.020 * o * original_weight, 0.03)
            second = bounded_add(second, 0.017 * o * original_weight, 0.025)
            third = bounded_add(third, 0.016 * o * original_weight, 0.025)
            notes.append(f"original_exhibition_stage:{stage_weights['stage']}:{original_weight:.2f}")

        return BoatProb(lane, player_id, course, win, second, third, win + second + third, notes)

    def _apply_slit_adjacency(
        self,
        boats: list[BoatProb],
        race: dict[str, Any],
    ) -> tuple[list[BoatProb], list[dict[str, Any]]]:
        """
        v1.4:
        Evaluate slit shape using actual entry order, not lane order.

        A boat is penalized only when exhibition slit delay and slow local ST agree.
        The boat immediately to its right is upgraded only when its slit and local ST
        are both superior. Exhibition ST alone never triggers a strong correction.
        """
        input_by_lane = {int(b["lane"]): b for b in race.get("boats", [])}
        ordered = sorted(boats, key=lambda b: b.entry_course)
        adjustments: list[dict[str, Any]] = []

        # Work on mutable copies.
        revised = [
            BoatProb(
                lane=b.lane,
                player_id=b.player_id,
                entry_course=b.entry_course,
                win=b.win,
                second=b.second,
                third=b.third,
                top3=b.top3,
                notes=list(b.notes),
            )
            for b in boats
        ]
        revised_by_lane = {b.lane: b for b in revised}

        for idx in range(len(ordered) - 1):
            left = ordered[idx]
            right = ordered[idx + 1]
            left_raw = input_by_lane.get(left.lane, {})
            right_raw = input_by_lane.get(right.lane, {})

            left_slit = left_raw.get("start_time")
            right_slit = right_raw.get("start_time")
            left_local_st = left_raw.get("local_st")
            right_local_st = right_raw.get("local_st")

            valid = all(
                v is not None and str(v).strip() not in {"", "-", "None"}
                for v in (left_slit, right_slit, left_local_st, right_local_st)
            )
            if not valid:
                continue

            left_slit = float(left_slit)
            right_slit = float(right_slit)
            left_local_st = float(left_local_st)
            right_local_st = float(right_local_st)

            slit_gap = left_slit - right_slit
            local_st_gap = left_local_st - right_local_st

            # Strong agreement: right boat is clearly ahead in the slit and locally starts faster.
            left_objectively_slow = left_local_st >= 0.18
            strong = (
                left_objectively_slow
                and slit_gap >= 0.08
                and local_st_gap >= 0.02
                and right_local_st <= 0.17
                and right.entry_course in (2, 3, 4)
            )
            # Moderate agreement: still requires both signals to point in the same direction.
            moderate = (
                left_objectively_slow
                and slit_gap >= 0.05
                and local_st_gap >= 0.01
                and right_local_st <= 0.18
                and right.entry_course in (2, 3, 4)
            )

            if not (strong or moderate):
                continue

            severity = 1.0 if strong else 0.62
            left_bp = revised_by_lane[left.lane]
            right_bp = revised_by_lane[right.lane]

            # Inner/wall boat loses head probability but can remain for second/third.
            left_win_down = 0.038 * severity
            left_second_down = 0.010 * severity
            left_third_up = 0.012 * severity

            # Attacking right-side boat gains head and second-place probability.
            right_win_up = 0.036 * severity
            right_second_up = 0.024 * severity
            right_third_up = 0.006 * severity

            left_bp.win = bounded_add(left_bp.win, -left_win_down, 0.040)
            left_bp.second = bounded_add(left_bp.second, -left_second_down, 0.014)
            left_bp.third = bounded_add(left_bp.third, left_third_up, 0.015)
            left_bp.notes.append("slit_local_st_left_penalty")

            right_bp.win = bounded_add(right_bp.win, right_win_up, 0.040)
            right_bp.second = bounded_add(right_bp.second, right_second_up, 0.030)
            right_bp.third = bounded_add(right_bp.third, right_third_up, 0.010)
            right_bp.notes.append("slit_local_st_right_attack_bonus")

            # Boat immediately outside the attacker receives a linkage boost.
            outside = None
            for candidate in ordered:
                if candidate.entry_course == right.entry_course + 1:
                    outside = revised_by_lane[candidate.lane]
                    break
            if outside is not None:
                outside.second = bounded_add(outside.second, 0.012 * severity, 0.016)
                outside.third = bounded_add(outside.third, 0.016 * severity, 0.020)
                outside.notes.append("slit_attack_direct_outside_link")

            adjustments.append(
                {
                    "left_lane": left.lane,
                    "left_course": left.entry_course,
                    "right_lane": right.lane,
                    "right_course": right.entry_course,
                    "slit_gap": round(slit_gap, 3),
                    "local_st_gap": round(local_st_gap, 3),
                    "strength": "strong" if strong else "moderate",
                    "left_win_delta": round(-left_win_down, 4),
                    "right_win_delta": round(right_win_up, 4),
                    "outside_lane": outside.lane if outside is not None else None,
                }
            )

        return revised, adjustments

    def _apply_escape_rate_multi_attack(
        self,
        boats: list[BoatProb],
        race: dict[str, Any],
    ) -> tuple[list[BoatProb], dict[str, Any]]:
        """
        v1.9:
        Reduce course-1 head probability when its escape rate is below 63%
        AND one or more actual courses 2-4 qualify as attack candidates.
        If multiple attackers qualify, all receive a proportional head bonus.
        """
        raw_by_lane = {int(b["lane"]): b for b in race.get("boats", [])}
        revised = [
            BoatProb(
                lane=b.lane, player_id=b.player_id, entry_course=b.entry_course,
                win=b.win, second=b.second, third=b.third, top3=b.top3,
                notes=list(b.notes),
            )
            for b in boats
        ]
        by_lane = {b.lane: b for b in revised}
        lane_by_course = {b.entry_course: b.lane for b in revised}

        audit: dict[str, Any] = {
            "active": False,
            "escape_rate": None,
            "escape_band": None,
            "attackers": [],
            "course1_win_delta": 0.0,
        }

        lane1 = lane_by_course.get(1)
        if lane1 is None:
            return revised, audit

        raw1 = raw_by_lane[lane1]
        escape = raw1.get("boaters_escape_rate")
        if escape is None or str(escape).strip() in {"", "-", "None"}:
            audit["reason"] = "escape_rate_missing"
            return revised, audit

        escape = float(escape)
        audit["escape_rate"] = escape
        if escape >= 70.0:
            audit["escape_band"] = "strong"
        elif escape >= 63.0:
            audit["escape_band"] = "above_average"
        elif escape >= 58.0:
            audit["escape_band"] = "neutral_to_weak"
        elif escape >= 50.0:
            audit["escape_band"] = "weak"
        else:
            audit["escape_band"] = "high_risk"

        if escape >= 63.0:
            audit["reason"] = "escape_rate_not_low"
            return revised, audit

        class_score_map = {"A1": 1.0, "A2": 0.68, "B1": 0.25, "B2": -0.10}
        attackers = []
        for course in (2, 3, 4):
            lane = lane_by_course.get(course)
            if lane is None:
                continue
            raw = raw_by_lane[lane]

            cls = class_score_map.get(str(raw.get("class") or "B1"), 0.25)
            nat_win = float(raw.get("nat_win") or 5.0)
            nat_2 = float(raw.get("nat_2") or 32.0)
            local_win = float(raw.get("local_win") or 0.0)
            local_2 = float(raw.get("local_2") or 0.0)
            motor2 = float(raw.get("motor_2") or 35.0)
            motor3 = float(raw.get("motor_3") or 52.0)
            exhibition = max(-1.0, min(1.0, float(raw.get("exhibition_score") or 0.0)))
            avg_st = float(raw.get("avg_st") or 0.18)
            local_st_raw = raw.get("local_st")
            actual_st = float(local_st_raw) if local_st_raw not in (None, "", "-") else avg_st

            ability = max(-1.0, min(1.0, ((nat_win - 5.2) / 1.8) * 0.58 + ((nat_2 - 35.0) / 15.0) * 0.42))
            local = 0.0
            if local_win > 0.01:
                local = max(-1.0, min(1.0, ((local_win - 5.2) / 1.8) * 0.60 + ((local_2 - 35.0) / 15.0) * 0.40))
            motor = max(-1.0, min(1.0, ((motor2 - 35.0) / 12.0) * 0.58 + ((motor3 - 52.0) / 16.0) * 0.42))
            st = max(-1.0, min(1.0, (0.18 - actual_st) / 0.05))

            course_base = {2: 0.18, 3: 0.30, 4: 0.24}[course]
            score = (
                0.23 * ability
                + 0.16 * local
                + 0.17 * exhibition
                + 0.12 * motor
                + 0.12 * st
                + 0.12 * cls
                + 0.08 * course_base
            )

            # Broad enough to allow two simultaneous attackers, but still requires
            # a positive multi-signal profile rather than exhibition alone.
            if score >= 0.08:
                attackers.append({
                    "lane": lane,
                    "course": course,
                    "score": score,
                    "details": {
                        "ability": round(ability, 4),
                        "local": round(local, 4),
                        "exhibition": round(exhibition, 4),
                        "motor": round(motor, 4),
                        "st": round(st, 4),
                        "class": round(cls, 4),
                    },
                })

        if not attackers:
            audit["reason"] = "no_qualified_attacker"
            return revised, audit

        # Escape-rate severity and multi-attacker expansion.
        escape_severity = max(0.0, min(1.0, (63.0 - escape) / 13.0))
        multi_bonus = 0.012 if len(attackers) >= 2 else 0.0
        total_head_shift = min(0.08, 0.030 + 0.038 * escape_severity + multi_bonus)

        inner = by_lane[lane1]
        inner.win = bounded_add(inner.win, -total_head_shift, 0.08)
        inner.second = bounded_add(inner.second, 0.006 + 0.006 * escape_severity, 0.014)
        inner.third = bounded_add(inner.third, 0.014 + 0.010 * escape_severity, 0.026)
        inner.notes.append("escape_rate_multi_attack_inner_down")

        positive_scores = [max(0.01, x["score"]) for x in attackers]
        score_sum = sum(positive_scores)
        for attacker, pos_score in zip(attackers, positive_scores):
            share = pos_score / score_sum
            bp = by_lane[attacker["lane"]]
            head_up = total_head_shift * share
            bp.win = bounded_add(bp.win, head_up, 0.06)
            bp.second = bounded_add(bp.second, 0.010 + 0.008 * share, 0.020)
            bp.notes.append("escape_rate_multi_attack_head_up")
            attacker["share"] = round(share, 4)
            attacker["win_delta"] = round(head_up, 4)

        audit.update({
            "active": True,
            "attackers": attackers,
            "course1_win_delta": round(-total_head_shift, 4),
            "multiple_attackers": len(attackers) >= 2,
        })
        return revised, audit

    def _apply_original_exhibition_and_outer_link(
        self,
        boats: list[BoatProb],
        race: dict[str, Any],
    ) -> tuple[list[BoatProb], dict[str, Any]]:
        """
        v1.7:
        1) Original exhibition is evaluated by within-race deviation from the six-boat mean.
        2) When course 4 is a meaningful second-place/attack candidate, compare course 5 and 6
           using class, motor, normal exhibition, original exhibition and local suitability.
        """
        raw_by_lane = {int(b["lane"]): b for b in race.get("boats", [])}
        revised = [
            BoatProb(
                lane=b.lane, player_id=b.player_id, entry_course=b.entry_course,
                win=b.win, second=b.second, third=b.third, top3=b.top3,
                notes=list(b.notes),
            )
            for b in boats
        ]
        by_lane = {b.lane: b for b in revised}
        audit: dict[str, Any] = {
            "original_exhibition_mean": None,
            "original_exhibition_adjustments": [],
            "course4_outer_link": None,
        }

        # Original exhibition: lower numeric values are assumed better.
        vals = []
        for lane, raw in raw_by_lane.items():
            v = raw.get("original_exhibition")
            if v is not None and str(v).strip() not in {"", "-", "None"}:
                vals.append(float(v))
        if len(vals) >= 4:
            mean_val = sum(vals) / len(vals)
            audit["original_exhibition_mean"] = round(mean_val, 4)
            for lane, raw in raw_by_lane.items():
                v = raw.get("original_exhibition")
                if v is None or str(v).strip() in {"", "-", "None"}:
                    continue
                value = float(v)
                # Positive = better than race mean.
                diff = mean_val - value
                score = max(-1.0, min(1.0, diff / 0.05))
                bp = by_lane[lane]
                bp.win = bounded_add(bp.win, 0.012 * score, 0.015)
                bp.second = bounded_add(bp.second, 0.016 * score, 0.020)
                bp.third = bounded_add(bp.third, 0.018 * score, 0.022)
                bp.notes.append("original_exhibition_vs_mean")
                audit["original_exhibition_adjustments"].append({
                    "lane": lane,
                    "value": value,
                    "diff_from_mean": round(diff, 4),
                    "score": round(score, 4),
                })

        # Identify actual courses 4, 5, 6.
        lane_by_course = {b.entry_course: b.lane for b in revised}
        lane4 = lane_by_course.get(4)
        lane5 = lane_by_course.get(5)
        lane6 = lane_by_course.get(6)
        if lane4 and lane5 and lane6:
            bp4 = by_lane[lane4]

            # Gate: course 4 must be a real second-place/attack candidate.
            second_rank = sorted(revised, key=lambda x: x.second, reverse=True)
            second_top3 = {b.lane for b in second_rank[:3]}
            course4_active = lane4 in second_top3 or bp4.second >= 0.14

            if course4_active:
                class_score_map = {"A1": 1.0, "A2": 0.65, "B1": 0.20, "B2": -0.15}
                composite = {}
                details = {}
                for lane in (lane5, lane6):
                    raw = raw_by_lane[lane]
                    cls = class_score_map.get(str(raw.get("class") or "B1"), 0.20)
                    m2 = float(raw.get("motor_2") or 35.0)
                    m3 = float(raw.get("motor_3") or 52.0)
                    motor = max(-1.0, min(1.0, ((m2 - 35.0) / 12.0) * 0.58 + ((m3 - 52.0) / 16.0) * 0.42))
                    exhibition = max(-1.0, min(1.0, float(raw.get("exhibition_score") or 0.0)))
                    original = 0.0
                    if audit["original_exhibition_mean"] is not None and raw.get("original_exhibition") is not None:
                        original = max(-1.0, min(1.0, (audit["original_exhibition_mean"] - float(raw["original_exhibition"])) / 0.05))
                    local = 0.0
                    if float(raw.get("local_win") or 0.0) > 0.01:
                        local = max(-1.0, min(1.0, (float(raw.get("local_win")) - 5.2) / 1.8))
                    score = 0.30 * cls + 0.25 * motor + 0.15 * exhibition + 0.15 * original + 0.10 * local + 0.05 * 0.0
                    composite[lane] = score
                    details[lane] = {
                        "class": round(cls, 4),
                        "motor": round(motor, 4),
                        "exhibition": round(exhibition, 4),
                        "original": round(original, 4),
                        "local": round(local, 4),
                        "composite": round(score, 4),
                    }

                better = lane6 if composite[lane6] > composite[lane5] else lane5
                worse = lane5 if better == lane6 else lane6
                gap = abs(composite[lane6] - composite[lane5])

                # Only apply a meaningful correction when the comparison is clear.
                if gap >= 0.08:
                    sev = min(1.0, 0.55 + gap)
                    bbetter = by_lane[better]
                    bworse = by_lane[worse]
                    bbetter.second = bounded_add(bbetter.second, 0.016 * sev, 0.020)
                    bbetter.third = bounded_add(bbetter.third, 0.028 * sev, 0.032)
                    bbetter.notes.append("course4_outer_composite_bonus")
                    bworse.third = bounded_add(bworse.third, -0.008 * sev, 0.010)
                    bworse.notes.append("course4_outer_composite_relative_down")
                    audit["course4_outer_link"] = {
                        "active": True,
                        "lane4": lane4,
                        "lane5": lane5,
                        "lane6": lane6,
                        "preferred_lane": better,
                        "score_gap": round(gap, 4),
                        "details": details,
                    }
                else:
                    audit["course4_outer_link"] = {
                        "active": False,
                        "reason": "composite_gap_too_small",
                        "details": details,
                    }

        return revised, audit

    def _scenario_probs(self, boats: list[BoatProb], race: dict[str, Any], water_type: str | None) -> dict[str, float]:
        w = np.array([b.win for b in boats])
        s = np.array([b.second for b in boats])
        # Mechanistic scenario scores, later normalized.
        score = {
            "nige": max(0.03, w[0] * 1.55),
            "sashi_2": max(0.02, w[1] * 1.10 + s[0] * 0.20),
            "makuri_3": max(0.02, w[2] * 1.10),
            "makurizashi_3": max(0.02, w[2] * 0.82 + s[3] * 0.10),
            "makuri_4": max(0.02, w[3] * 1.08),
            "makurizashi_4": max(0.02, w[3] * 0.83 + s[4] * 0.10),
            "outer_attack": max(0.015, (w[4] + w[5]) * 0.75),
            "chaos": 0.07,
        }
        wind_speed = float(race.get("wind_speed") or 0.0)
        wave_height = float(race.get("wave_height") or 0.0)
        if wind_speed >= 4:
            score["chaos"] += 0.035 + max(0.0, wind_speed - 4.0) * 0.008
            score["outer_attack"] += 0.018
            score["makuri_3"] += 0.012
            score["makurizashi_3"] += 0.010
        if wave_height >= 4:
            score["chaos"] += 0.018

        # v1.1: use actual entry shape to identify the attacking boat and its direct outside boat.
        ordered = sorted(boats, key=lambda b: b.entry_course)
        if any(b.entry_course != b.lane for b in boats):
            # Inner-course movers with good win score become explicit attack candidates.
            for pos, b in enumerate(ordered):
                if b.lane != 1 and b.entry_course in (2, 3):
                    if b.entry_course == 2:
                        score["sashi_2"] += b.win * 0.55
                    else:
                        score["makuri_3"] += b.win * 0.45
                        score["makurizashi_3"] += b.win * 0.30
            score["chaos"] += 0.035
        if water_type == "イン逃げ安定型":
            score["nige"] *= 1.20
        elif water_type == "4浮上型":
            score["makuri_4"] *= 1.16
            score["makurizashi_4"] *= 1.16
        elif water_type == "3攻め型":
            score["makuri_3"] *= 1.18
            score["makurizashi_3"] *= 1.18
        elif water_type == "2差し浮上型":
            score["sashi_2"] *= 1.18

        vals = normalize(np.array(list(score.values())))
        return {k: float(v) for k, v in zip(score, vals)}

    @staticmethod
    def _combo_probability(a: int, b: int, c: int, win: np.ndarray, second: np.ndarray, third: np.ndarray) -> float:
        # Sequential Plackett-Luce-like score from position-specific probabilities.
        p1 = win[a]
        rem2 = second.copy()
        rem2[a] = 0
        p2 = rem2[b] / max(rem2.sum(), 1e-9)
        rem3 = third.copy()
        rem3[[a, b]] = 0
        p3 = rem3[c] / max(rem3.sum(), 1e-9)
        return float(p1 * p2 * p3)

    def _scenario_multiplier(
        self,
        combo: tuple[int, int, int],
        scenarios: dict[str, float],
        entry_course_by_lane: dict[int, int],
        preferred_course4_outer_lane: int | None = None,
        attack_lanes: set[int] | None = None,
    ) -> tuple[float, list[str]]:
        a, b, c = [x + 1 for x in combo]
        head_course = entry_course_by_lane.get(a, a)
        mult = 1.0
        tags = []
        if a == 1:
            mult *= 1 + scenarios["nige"] * 0.16
            tags.append("nige")
        if head_course == 2:
            mult *= 1 + scenarios["sashi_2"] * 0.22
            tags.append("course2_attack")
        if head_course == 3:
            mult *= 1 + (scenarios["makuri_3"] + scenarios["makurizashi_3"]) * 0.19
            tags.append("course3_attack")
        if head_course == 4:
            mult *= 1 + (scenarios["makuri_4"] + scenarios["makurizashi_4"]) * 0.18
            tags.append("course4_attack")

        # Link to the boat directly outside in actual entry order.
        outside_lane = None
        for lane, course in entry_course_by_lane.items():
            if course == head_course + 1:
                outside_lane = lane
                break
        if outside_lane is not None and outside_lane in (b, c):
            mult *= 1.12
            tags.append("direct_outside_link")
        if a != 1 and 1 in (b, c):
            mult *= 1.08
            tags.append("inside_remain")

        # v1.8: when actual course 4 is the second-place boat, use the precomputed
        # 5-vs-6 composite comparison to rank the linked third-place candidate.
        second_course = entry_course_by_lane.get(b, b)
        if second_course == 4 and preferred_course4_outer_lane is not None:
            if c == preferred_course4_outer_lane:
                mult *= 1.34
                tags.append("course4_preferred_outer_link")
            elif entry_course_by_lane.get(c, c) in (5, 6):
                mult *= 0.94
                tags.append("course4_nonpreferred_outer")
        if attack_lanes and (a + 1) in attack_lanes:
            head_course = entry_course_by_lane.get(a + 1, a + 1)
            outside_lane = next(
                (lane for lane, course in entry_course_by_lane.items() if course == head_course + 1),
                None,
            )
            if outside_lane is not None and (b + 1) == outside_lane and (c + 1) == 1:
                mult *= 1.30
                tags.append("attack_head_outside_second_inside_third")
            elif outside_lane is not None and (b + 1) == 1 and (c + 1) == outside_lane:
                mult *= 0.98
                tags.append("attack_head_inside_second_outside_third")

        if a in (5, 6):
            mult *= 1 + scenarios["outer_attack"] * 0.14
            tags.append("outer_attack")
        if scenarios["chaos"] >= 0.13 and a != 1:
            mult *= 1.05
            tags.append("chaos")
        return mult, tags

    def _tickets(
        self,
        win: np.ndarray,
        second: np.ndarray,
        third: np.ndarray,
        scenarios: dict[str, float],
        entry_course_by_lane: dict[int, int],
        preferred_course4_outer_lane: int | None = None,
        attack_lanes: set[int] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        combos = []
        for combo in itertools.permutations(range(6), 3):
            raw = self._combo_probability(*combo, win, second, third)
            mult, tags = self._scenario_multiplier(
                combo,
                scenarios,
                entry_course_by_lane,
                preferred_course4_outer_lane,
                attack_lanes,
            )
            combos.append({
                "ticket": f"{combo[0]+1}-{combo[1]+1}-{combo[2]+1}",
                "probability": raw * mult,
                "head": combo[0] + 1,
                "tags": tags,
            })
        total = sum(x["probability"] for x in combos)
        for x in combos:
            x["probability"] /= total
        combos.sort(key=lambda x: x["probability"], reverse=True)

        selected = set()
        main = []

        # v1.5: third-place gate.
        # For the strongest head-second pair, compare all remaining boats with
        # third probability >=10% and preserve the top three candidates.
        top_head = int(combos[0]["ticket"].split("-")[0])
        same_head = [x for x in combos if int(x["ticket"].split("-")[0]) == top_head]
        top_second = int(same_head[0]["ticket"].split("-")[1])
        third_candidates = []
        for lane_idx, p3 in enumerate(third, start=1):
            if lane_idx in (top_head, top_second) or p3 < 0.10:
                continue
            ticket = f"{top_head}-{top_second}-{lane_idx}"
            match = next((x for x in combos if x["ticket"] == ticket), None)
            if match is not None:
                third_candidates.append((float(p3), match))
        third_candidates.sort(key=lambda item: item[0], reverse=True)
        for _, x in third_candidates[:4]:
            if x["ticket"] not in selected:
                main.append(x)
                selected.add(x["ticket"])

        for x in combos:
            if len(main) >= 6:
                break
            if x["ticket"] not in selected:
                main.append(x)
                selected.add(x["ticket"])

        # Deviation: preserve main head candidates but cover 2/3 reversal or attack stopping at second.
        main_heads = {x["head"] for x in main[:4]}
        deviation = []
        for x in combos:
            if len(deviation) >= 2:
                break
            if x["ticket"] in selected:
                continue
            if x["head"] in main_heads and ("3to4_link" in x["tags"] or "4to56_link" in x["tags"] or x["probability"] >= combos[12]["probability"]):
                deviation.append(x)
                selected.add(x["ticket"])

        # Upset: prioritize coherent attack-head + direct-outside + inside-remain structures.
        upset = []
        coherent_priority = []
        for x in combos:
            parts = [int(v) for v in x["ticket"].split("-")]
            if parts[0] != 1 and "inside_remain" in x["tags"] and "direct_outside_link" in x["tags"]:
                coherent_priority.append(x)
        coherent_priority.sort(key=lambda x: x["probability"], reverse=True)
        for pool in (coherent_priority, combos):
            for x in pool:
                if len(upset) >= 2:
                    break
                if x["ticket"] in selected or x["head"] == 1:
                    continue
                if any(t in x["tags"] for t in ["course2_attack", "course3_attack", "course4_attack", "outer_attack", "chaos"]):
                    upset.append(x)
                    selected.add(x["ticket"])
            if len(upset) >= 2:
                break

        # Guaranteed fill while preserving 6/2/2 and uniqueness.
        for bucket, need in [(deviation, 2), (upset, 2)]:
            for x in combos:
                if len(bucket) >= need:
                    break
                if x["ticket"] not in selected:
                    bucket.append(x)
                    selected.add(x["ticket"])

        def clean(rows):
            return [
                {
                    "ticket": r["ticket"],
                    "probability": round(r["probability"], 6),
                    "scenario_tags": r["tags"],
                }
                for r in rows
            ]
        return {"main": clean(main), "deviation": clean(deviation), "upset": clean(upset)}

    @staticmethod
    def _sab(
        win: np.ndarray,
        scenarios: dict[str, float],
        tickets: dict[str, list[dict[str, Any]]],
        entry_changed: bool,
        entry_change_severity: float = 0.0,
        event_day: int = 1,
        escape_attack_audit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        order = np.sort(win)[::-1]
        head_gap = float(order[0] - order[1])
        head_concentration = float(order[0])
        scenario_top = max(scenarios.values())
        top10_cover = sum(x["probability"] for k in tickets for x in tickets[k])
        score = (
            0.34 * min(1.0, head_concentration / 0.52)
            + 0.24 * min(1.0, head_gap / 0.22)
            + 0.24 * min(1.0, scenario_top / 0.55)
            + 0.18 * min(1.0, top10_cover / 0.45)
        )
        if entry_changed:
            score -= 0.07 + min(0.15, 0.10 * entry_change_severity)
        if event_day == 1:
            score -= 0.035
        attack_penalty = 0.0
        if escape_attack_audit and escape_attack_audit.get("active"):
            attack_count = len(escape_attack_audit.get("attackers", []))
            attack_penalty = 0.075 + (0.035 if attack_count >= 2 else 0.0)
            score -= attack_penalty
        if score >= 0.70:
            grade = "S"
        elif score >= 0.52:
            grade = "A"
        else:
            grade = "B"

        # Low escape rate plus at least one qualified attacker means the race
        # contains a concrete non-1 head branch, so S is capped at A.
        sab_grade_cap = None
        if escape_attack_audit and escape_attack_audit.get("active"):
            sab_grade_cap = "A"
            if grade == "S":
                grade = "A"
        return {
            "grade": grade,
            "score": round(max(0.0, min(1.0, score)), 4),
            "head_concentration": round(head_concentration, 4),
            "head_gap": round(head_gap, 4),
            "scenario_top": round(float(scenario_top), 4),
            "ticket_coverage_10": round(float(top10_cover), 4),
            "entry_change_penalty": bool(entry_changed),
            "entry_change_severity": round(float(entry_change_severity), 4),
            "first_day_penalty": bool(event_day == 1),
            "low_escape_attack_penalty": round(float(attack_penalty), 4),
            "grade_cap": sab_grade_cap,
            "meaning": "prediction reproducibility/confidence; independent from ticket count",
        }

    def predict(self, race: dict[str, Any]) -> dict[str, Any]:
        boats_in = race.get("boats") or []
        if len(boats_in) != 6:
            raise ValueError("boats must contain exactly six entries")
        lanes = sorted(int(b["lane"]) for b in boats_in)
        if lanes != [1, 2, 3, 4, 5, 6]:
            raise ValueError("lanes must be exactly 1..6")

        time_band = race.get("time_band") or time_band_from_race(int(race["race_no"]), race.get("start_time"))
        tide_type = race.get("tide_type")
        enriched_boats = []
        for b in boats_in:
            x = dict(b)
            x.setdefault("event_day", race.get("event_day", 1))
            x.setdefault("race_stage", race.get("race_stage"))
            x.setdefault("is_semifinal", race.get("is_semifinal", False))
            x.setdefault("is_final", race.get("is_final", False))
            x.setdefault("minutes_to_low_tide", race.get("minutes_to_low_tide"))
            x.setdefault("tide_phase", race.get("tide_phase"))
            enriched_boats.append(x)
        boat_probs = [self._base_probs(b, time_band, tide_type) for b in sorted(enriched_boats, key=lambda x: int(x["lane"]))]
        boat_probs, slit_adjacency_adjustments = self._apply_slit_adjacency(boat_probs, {"boats": enriched_boats})
        boat_probs, escape_attack_audit = self._apply_escape_rate_multi_attack(
            boat_probs, {"boats": enriched_boats}
        )
        boat_probs, original_outer_audit = self._apply_original_exhibition_and_outer_link(
            boat_probs, {"boats": enriched_boats}
        )

        win = normalize(np.array([b.win for b in boat_probs]), 1.0)
        second = normalize(np.array([b.second for b in boat_probs]), 1.0)
        third = normalize(np.array([b.third for b in boat_probs]), 1.0)

        condition_key = f"{tide_type}×{time_band}" if tide_type else None
        cond = self.water_cond[self.water_cond["condition_key"].astype(str) == str(condition_key)]
        water_type = None if cond.empty else str(cond.iloc[0].get("water_type"))

        scenarios = self._scenario_probs(boat_probs, race, water_type)
        entry_course_by_lane = {int(b["lane"]): int(b.get("entry_course") or b["lane"]) for b in boats_in}
        preferred_course4_outer_lane = None
        outer_link = original_outer_audit.get("course4_outer_link") or {}
        if outer_link.get("active"):
            preferred_course4_outer_lane = int(outer_link.get("preferred_lane"))
        attack_lanes = {
            int(x["lane"]) for x in escape_attack_audit.get("attackers", [])
        } if escape_attack_audit.get("active") else set()
        tickets = self._tickets(
            win,
            second,
            third,
            scenarios,
            entry_course_by_lane,
            preferred_course4_outer_lane,
            attack_lanes,
        )
        entry_changed = any(int(b.get("entry_course") or b["lane"]) != int(b["lane"]) for b in boats_in)
        moved = [abs(int(b.get("entry_course") or b["lane"]) - int(b["lane"])) for b in boats_in]
        entry_change_severity = min(1.0, (sum(1 for d in moved if d > 0) / 6.0) * 0.6 + (sum(moved) / 15.0) * 0.4)
        sab = self._sab(
            win,
            scenarios,
            tickets,
            entry_changed,
            entry_change_severity,
            int(race.get("event_day") or 1),
            escape_attack_audit,
        )

        stage_weighting = self._event_stage_weights(
            int(race.get("event_day") or 1),
            race,
        )
        meeting_form_audit = []
        for raw in sorted(enriched_boats, key=lambda x: int(x["lane"])):
            meeting_form_audit.append({
                "lane": int(raw["lane"]),
                "player_id": raw.get("player_id"),
                **self._derive_meeting_form(raw),
            })

        probabilities = []
        for i, bp in enumerate(boat_probs):
            probabilities.append({
                "lane": bp.lane,
                "player_id": bp.player_id,
                "entry_course": bp.entry_course,
                "win": round(float(win[i]), 6),
                "second": round(float(second[i]), 6),
                "third": round(float(third[i]), 6),
                "top3": round(float(win[i] + second[i] + third[i]), 6),
                "notes": bp.notes,
            })

        checks = {
            "six_boats": len(probabilities) == 6,
            "win_sum": round(sum(x["win"] for x in probabilities), 6),
            "second_sum": round(sum(x["second"] for x in probabilities), 6),
            "third_sum": round(sum(x["third"] for x in probabilities), 6),
            "ticket_count": sum(len(v) for v in tickets.values()),
            "ticket_unique": len({x["ticket"] for v in tickets.values() for x in v}) == 10,
            "odds_used": False,
        }

        return {
            "venue": "wakamatsu",
            "date": race.get("date"),
            "race_no": int(race["race_no"]),
            "logic_version": "wakamatsu_engine_v2.0",
            "engine_type": "data-calibrated rules engine; original trained model unavailable",
            "full_reflection": {
                "player_course_db": True,
                "observed_tide_water_db": True,
                "tide_noleak_oof_calibration": True,
                "scenario_rules": True,
                "meeting_motor_exhibition": "separate stage-dependent scoring; meeting trend derived when runs supplied",
            },
            "race_context": {
                "time_band": time_band,
                "tide_type": tide_type,
                "tide_phase": race.get("tide_phase"),
                "tide_zone": race.get("tide_zone"),
                "water_type": water_type,
                "wind_dir": race.get("wind_dir"),
                "wind_speed": race.get("wind_speed"),
                "wave_height": race.get("wave_height"),
                "entry_changed": entry_changed,
                "event_day": int(race.get("event_day") or 1),
                "race_stage": stage_weighting["stage"],
                "minutes_to_low_tide": race.get("minutes_to_low_tide"),
            },
            "probabilities": probabilities,
            "stage_weighting": stage_weighting,
            "meeting_form_audit": meeting_form_audit,
            "scenarios": dict(sorted(scenarios.items(), key=lambda kv: kv[1], reverse=True)),
            "slit_adjacency_adjustments": slit_adjacency_adjustments,
            "escape_rate_multi_attack": escape_attack_audit,
            "original_exhibition_outer_link": original_outer_audit,
            "sab": sab,
            "tickets": tickets,
            "third_place_gate": {
                "threshold": 0.10,
                "rule": "top head-second pair preserves up to four highest third-rate candidates"
            },
            "validation": checks,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    race = json.loads(Path(args.input).read_text(encoding="utf-8"))
    engine = WakamatsuEngine(args.db)
    try:
        result = engine.predict(race)
    finally:
        engine.close()
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
