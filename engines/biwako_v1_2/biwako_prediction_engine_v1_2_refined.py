#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
BASE_CANDIDATES = [
    HERE / "biwako_prediction_engine_v1_2.py",
    HERE.parent / "biwako_v1_2" / "biwako_prediction_engine_v1_2.py",
]
BASE = next((p for p in BASE_CANDIDATES if p.exists()), None)
if BASE is None:
    raise FileNotFoundError("biwako_prediction_engine_v1_2.py not found")

spec = importlib.util.spec_from_file_location("biwako_v12_base", BASE)
base12 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = base12
spec.loader.exec_module(base12)

# v1.2 itself imports v1.1 as module-global `base`.
base = base12.base


class BiwakoPredictionEngineV12Refined(base12.BiwakoPredictionEngineV12):
    """
    びわこAI v1.2 refined candidate.

    Goals
    -----
    * Keep the proven v1.2 structure intact.
    * Correct persistent over-strength of C1 without globally destroying
      Biwako's inside-course advantage.
    * Never use odds or race results as prediction inputs.
    * Keep exhibition ST as a supporting/shape signal, not a large standalone
      coefficient.

    Fixed candidate changes
    -----------------------
    1) C1 normal win prior: 54.5403% -> 52.0%.
       The removed mass is redistributed proportionally across C2-C6 so the
       six-course win prior remains exactly 1.0.
    2) Strong C2/C3/C4 attack scenario:
       only when C1 final pre-scenario win is 35-60%, at least one C2-C4 head
       is >=15%, and max(2_sashi, 3_attack, 4_attack) >=0.27.
       C1 receives an additional -4 to -8 percentage-point reassessment.
       Removed mass is distributed across C2-C4 by current head strength
       x attack-scenario strength.
    3) Conditional second:
       active C2/C3/C4 head branches use a 60% historical P(second|first)
       blend.
    4) Conditional third:
       only active attack heads use head-specific blend:
         C2=35%, C3=20%, C4=35%.
       Other branches remain model-driven for this refinement layer.
    5) If C1 <45% and a strong attack is active, C1-head tickets are capped at
       two. Freed slots are filled primarily by C2-C4 joint/scenario score.
    """

    ENGINE_VERSION = "biwako_engine_v1.2_refined"
    PARAMETER_VERSION = "biwako_v1.2_refined_20260831"

    # ---- Normal course prior -------------------------------------------------
    C1_WIN_PRIOR = 0.52

    # ---- Strong-attack probability reassessment -----------------------------
    STRONG_ATTACK_C1_MIN = 0.35
    STRONG_ATTACK_C1_MAX = 0.60
    STRONG_ATTACK_HEAD_MIN = 0.15
    STRONG_ATTACK_SCORE_MIN = 0.27
    STRONG_ATTACK_SCORE_FULL = 0.42
    STRONG_ATTACK_PENALTY_MIN = 0.04   # probability points, not log units
    STRONG_ATTACK_PENALTY_MAX = 0.08

    # ---- Conditional order ---------------------------------------------------
    SECOND_ACTIVE_BLEND = 0.60
    THIRD_BLEND_BY_HEAD = {2: 0.35, 3: 0.20, 4: 0.35}

    # ---- Ticket diversity ----------------------------------------------------
    C1_TICKET_CAP_THRESHOLD = 0.45
    C1_TICKET_CAP = 2

    def __init__(self, db_path, config_path):
        super().__init__(db_path, config_path)

        # Preserve original priors for exact proportional redistribution.
        original = {
            c: float(self.cfg["course_prior"][str(c)]["win"])
            for c in range(1, 7)
        }
        old_c1 = original[1]
        outer_old = sum(original[c] for c in range(2, 7))
        outer_new = 1.0 - self.C1_WIN_PRIOR
        scale = outer_new / outer_old

        self._refined_win_prior = {1: self.C1_WIN_PRIOR}
        for c in range(2, 7):
            self._refined_win_prior[c] = original[c] * scale

        # Head-specific historical-third blend coefficients.
        # v1.2 chooses strong/medium/weak blend from sample count; this subclass
        # uses synthetic effective sample tiers only for active C2-C4 branches.
        cond = self.cfg["fixed_parameters"]["conditional_order"]
        cond["blend_strong"] = self.THIRD_BLEND_BY_HEAD[2]  # 0.35 (C2/C4)
        cond["blend_medium"] = self.THIRD_BLEND_BY_HEAD[3]  # 0.20 (C3)
        cond["blend_weak"] = 0.0
        cond["blend_none"] = 0.0

        # v1.2's second-order strong blend is overridden to 60%.
        self.SECOND_BLEND_STRONG = self.SECOND_ACTIVE_BLEND
        self.SECOND_BLEND_MEDIUM = self.SECOND_ACTIVE_BLEND
        self.SECOND_BLEND_WEAK = self.SECOND_ACTIVE_BLEND

        self._refined_active_attack = False
        self._refined_active_heads = set()
        self._refined_attack_meta: Dict[str, Any] = {}

    # -------------------------------------------------------------------------
    # 52% normal C1 prior
    # -------------------------------------------------------------------------
    def _course_prior(self, course: int, pos: str) -> float:
        if pos == "win":
            return float(self._refined_win_prior[int(course)])
        return super()._course_prior(course, pos)

    # -------------------------------------------------------------------------
    # Scenario-aware probability reassessment before scenario/ticket building
    # -------------------------------------------------------------------------
    @staticmethod
    def _scenario_raw_scores(
        probs: Dict[int, Dict[str, float]],
        states: List[Any],
    ) -> Dict[int, float]:
        course_to_lane = {int(s.course): int(s.lane) for s in states}

        def p(course: int, pos: str) -> float:
            lane = course_to_lane.get(course)
            if lane is None:
                return 0.0
            return float(probs.get(lane, {}).get(pos, 0.0))

        return {
            2: 1.08 * p(2, "win") + 0.32 * p(1, "second"),
            3: 1.05 * p(3, "win") + 0.38 * p(3, "second"),
            4: 1.08 * p(4, "win") + 0.40 * p(4, "second"),
        }

    def _marginals(self, states):
        probs = super()._marginals(states)
        course_to_lane = {int(s.course): int(s.lane) for s in states}

        lane1 = course_to_lane.get(1)
        if lane1 is None:
            self._refined_active_attack = False
            self._refined_active_heads = set()
            self._refined_attack_meta = {"active": False, "reason": "no_course1"}
            return probs

        p1 = float(probs[lane1]["win"])
        scores = self._scenario_raw_scores(probs, states)

        eligible_heads = {
            c for c in (2, 3, 4)
            if course_to_lane.get(c) is not None
            and float(probs[course_to_lane[c]]["win"]) >= self.STRONG_ATTACK_HEAD_MIN
        }
        max_score = max((scores[c] for c in eligible_heads), default=0.0)

        active = (
            self.STRONG_ATTACK_C1_MIN <= p1 <= self.STRONG_ATTACK_C1_MAX
            and bool(eligible_heads)
            and max_score >= self.STRONG_ATTACK_SCORE_MIN
        )

        if not active:
            self._refined_active_attack = False
            self._refined_active_heads = set()
            self._refined_attack_meta = {
                "active": False,
                "c1_before": p1,
                "scenario_scores": scores,
                "eligible_heads": sorted(eligible_heads),
                "max_score": max_score,
            }
            return probs

        strength = (max_score - self.STRONG_ATTACK_SCORE_MIN) / max(
            1e-12, self.STRONG_ATTACK_SCORE_FULL - self.STRONG_ATTACK_SCORE_MIN
        )
        strength = max(0.0, min(1.0, strength))
        penalty = (
            self.STRONG_ATTACK_PENALTY_MIN
            + strength
            * (self.STRONG_ATTACK_PENALTY_MAX - self.STRONG_ATTACK_PENALTY_MIN)
        )
        penalty = min(penalty, max(0.0, p1 - 0.01))

        # Redistribute only to C2-C4, using current head probability × scenario.
        weights: Dict[int, float] = {}
        for c in (2, 3, 4):
            lane = course_to_lane.get(c)
            if lane is None:
                continue
            weights[c] = max(
                0.0,
                float(probs[lane]["win"]) * max(0.0, scores.get(c, 0.0)),
            )

        denom = sum(weights.values())
        if denom <= 0:
            self._refined_active_attack = False
            self._refined_active_heads = set()
            self._refined_attack_meta = {
                "active": False,
                "reason": "zero_redistribution_weight",
                "c1_before": p1,
                "scenario_scores": scores,
            }
            return probs

        probs[lane1]["win"] = p1 - penalty
        additions: Dict[int, float] = {}
        for c, w in weights.items():
            lane = course_to_lane[c]
            add = penalty * w / denom
            probs[lane]["win"] += add
            additions[c] = add

        # Numerical guard: preserve exact probability sum.
        win_sum = sum(float(v["win"]) for v in probs.values())
        if win_sum > 0:
            for lane in probs:
                probs[lane]["win"] = float(probs[lane]["win"]) / win_sum

        active_heads = {
            c for c in eligible_heads
            if scores[c] >= self.STRONG_ATTACK_SCORE_MIN
        }
        # If only one branch clears 0.27, still keep other >=15% C2-C4 branches
        # eligible for conditional order, but do not manufacture C5/C6 heads.
        if not active_heads:
            active_heads = set(eligible_heads)

        self._refined_active_attack = True
        self._refined_active_heads = set(active_heads)
        self._refined_attack_meta = {
            "active": True,
            "c1_before": p1,
            "c1_after": float(probs[lane1]["win"]),
            "penalty_probability": penalty,
            "scenario_scores": scores,
            "eligible_heads": sorted(eligible_heads),
            "active_heads": sorted(active_heads),
            "redistribution": additions,
            "max_score": max_score,
        }
        return probs

    # -------------------------------------------------------------------------
    # Conditional second: 60% only on active C2/C3/C4 attack heads.
    # v1.2 determines blend from sample count, so return a controlled effective
    # n while preserving the real empirical probability q.
    # -------------------------------------------------------------------------
    def _conditional_second_db(self, first_course: int):
        result = super()._conditional_second_db(first_course)
        if not result:
            return result

        if self._refined_active_attack and first_course in self._refined_active_heads:
            effective_n = max(self.SECOND_N_STRONG, 100)
        else:
            effective_n = 0

        return {c: (q, effective_n) for c, (q, _n) in result.items()}

    # -------------------------------------------------------------------------
    # Conditional third: C2=35%, C3=20%, C4=35% only on active attack heads.
    # The inherited v1.2 code maps n->blend. We map active branches to:
    #   C2/C4 -> strong tier (35%)
    #   C3    -> medium tier (20%)
    # inactive branches -> none.
    # -------------------------------------------------------------------------
    def _conditional_order_db(self, first_course: int, second_course: int):
        result = super()._conditional_order_db(first_course, second_course)
        if not result:
            return result

        if not self._refined_active_attack or first_course not in self._refined_active_heads:
            effective_n = 0
        elif first_course in (2, 4):
            effective_n = max(
                int(self.cfg["fixed_parameters"]["conditional_order"]["n_strong"]),
                100,
            )
        elif first_course == 3:
            effective_n = max(
                int(self.cfg["fixed_parameters"]["conditional_order"]["n_medium"]),
                30,
            )
            n_strong = int(self.cfg["fixed_parameters"]["conditional_order"]["n_strong"])
            if effective_n >= n_strong:
                effective_n = max(1, n_strong - 1)
        else:
            effective_n = 0

        return {c: (q, effective_n) for c, (q, _n) in result.items()}

    # -------------------------------------------------------------------------
    # Ticket cap: C1 <45% + strong attack => max 2 C1-head tickets.
    # -------------------------------------------------------------------------
    def _head_slot_targets(self, probs, joint):
        slots = super()._head_slot_targets(probs, joint)
        if not self._refined_active_attack:
            return slots

        # Recover lane<->course mapping from joint rows; supports entry changes.
        lane_for_course: Dict[int, int] = {}
        for row in joint[:20]:
            for lane, course in zip(row.get("lanes", ()), row.get("courses", ())):
                lane_for_course.setdefault(int(course), int(lane))

        c1_lane = lane_for_course.get(1)
        if c1_lane is None:
            return slots
        if float(probs[c1_lane]["win"]) >= self.C1_TICKET_CAP_THRESHOLD:
            return slots
        if slots.get(c1_lane, 0) <= self.C1_TICKET_CAP:
            return slots

        freed = slots[c1_lane] - self.C1_TICKET_CAP
        slots[c1_lane] = self.C1_TICKET_CAP

        # Refill primarily among C2-C4 by best next available joint score.
        preferred = {
            lane_for_course[c]
            for c in (2, 3, 4)
            if c in lane_for_course
        }

        for _ in range(freed):
            best = None
            # First pass: C2-C4.
            for lane in preferred:
                rows = [
                    r for r in joint
                    if r["lanes"][0] == lane and r["score"] > self.PROB_FLOOR
                ]
                idx = slots.get(lane, 0)
                if idx < len(rows):
                    cand = rows[idx]
                    key = (float(cand["score"]), float(probs[lane]["win"]))
                    if best is None or key > best[0]:
                        best = (key, lane)

            # Fallback: any non-C1 head, but never force an outer head.
            if best is None:
                for lane in probs:
                    if lane == c1_lane:
                        continue
                    rows = [
                        r for r in joint
                        if r["lanes"][0] == lane and r["score"] > self.PROB_FLOOR
                    ]
                    idx = slots.get(lane, 0)
                    if idx < len(rows):
                        cand = rows[idx]
                        key = (float(cand["score"]), float(probs[lane]["win"]))
                        if best is None or key > best[0]:
                            best = (key, lane)

            if best is None:
                break
            slots[best[1]] = slots.get(best[1], 0) + 1

        return slots

    # -------------------------------------------------------------------------
    # Metadata/audit trail
    # -------------------------------------------------------------------------
    def predict(self, race, stage="preliminary"):
        out = super().predict(race, stage)
        out["engine_version"] = self.ENGINE_VERSION
        out["parameter_version"] = self.PARAMETER_VERSION

        rules = out.setdefault("rules", {})
        rules["c1_prior_refinement"] = {
            "normal_c1_win_prior": self.C1_WIN_PRIOR,
            "redistributed_win_prior": {
                str(c): self._refined_win_prior[c] for c in range(1, 7)
            },
            "strong_attack_c1_band": [
                self.STRONG_ATTACK_C1_MIN,
                self.STRONG_ATTACK_C1_MAX,
            ],
            "strong_attack_head_min": self.STRONG_ATTACK_HEAD_MIN,
            "strong_attack_score_min": self.STRONG_ATTACK_SCORE_MIN,
            "penalty_probability_min": self.STRONG_ATTACK_PENALTY_MIN,
            "penalty_probability_max": self.STRONG_ATTACK_PENALTY_MAX,
            "results_used": False,
            "odds_used": False,
        }
        rules["conditional_second_refined"] = {
            "active_heads": [2, 3, 4],
            "blend": self.SECOND_ACTIVE_BLEND,
        }
        rules["conditional_third_refined"] = {
            "blend_by_head": {
                str(k): v for k, v in self.THIRD_BLEND_BY_HEAD.items()
            }
        }
        rules["ticket_cap_refined"] = {
            "when_c1_below": self.C1_TICKET_CAP_THRESHOLD,
            "requires_strong_attack": True,
            "c1_head_max": self.C1_TICKET_CAP,
        }

        out["refinement"] = dict(self._refined_attack_meta)
        return out


# Compatibility aliases
BiwakoPredictionEngine = BiwakoPredictionEngineV12Refined
BiwakoPredictionEngineV12Candidate = BiwakoPredictionEngineV12Refined
