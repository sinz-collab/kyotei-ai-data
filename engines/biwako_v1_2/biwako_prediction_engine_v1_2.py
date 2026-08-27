#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import itertools
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
BASE_CANDIDATES = [
    HERE / "biwako_prediction_engine_v1_1.py",
    HERE.parent / "biwako_v1_1" / "biwako_prediction_engine_v1_1.py",
]
BASE = next((p for p in BASE_CANDIDATES if p.exists()), None)
if BASE is None:
    raise FileNotFoundError("biwako_prediction_engine_v1_1.py not found")

spec = importlib.util.spec_from_file_location("biwako_v11_base", BASE)
base = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class BiwakoPredictionEngineV12(base.BiwakoPredictionEngineV11):
    """びわこAI v1.2 production candidate.

    v1.1の確率構築を継承し、2026-08-26までに固定した改善だけを実装する。

    禁止:
      - odds/result を予想に使用しない
      - 同日結果による補正をしない
      - 展示STを単独の強い係数にしない

    変更:
      1. motor = cumulative 70% + recent10 30%
      2. day1 machine motor factor = 0.70 (setsukan 0.20は維持)
      3. weak-C1 + multi-attacker gate
      4. C5/C6 attack-defense extension with attenuation
      5. current-layout P(second|first) blend
      6. C4 attack時のC5追走はスリット連動+基礎2/3着力がある場合のみ小補正
      7. 頭率による10点枠配分 + 頭ごとのjoint/scenario順位
      8. 強軸時、外攻めがある場合だけ最大1点の展開ヘッジ
    """

    ENGINE_VERSION = "biwako_engine_v1.2"
    PARAMETER_VERSION = "biwako_v1.2_20260827"

    # conditional second
    SECOND_BLEND_STRONG = 0.30
    SECOND_BLEND_MEDIUM = 0.18
    SECOND_BLEND_WEAK = 0.08
    SECOND_N_STRONG = 100
    SECOND_N_MEDIUM = 30
    SECOND_N_WEAK = 10

    # attack-defense
    OUTER_ATTACK_WEIGHT = {2: 1.00, 3: 1.00, 4: 1.00, 5: 0.55, 6: 0.35}
    OUTER_ATTACK_MIN_SCORE = 0.01
    WEAK_C1_ESCAPE_MAX = 0.48
    WEAK_C1_VULNERABILITY_MIN = 0.35
    WEAK_C1_CORE_ATTACKERS_MIN = 2
    WEAK_C1_ATTACK_SCORE_MIN = 0.01
    WEAK_C1_PENALTY_MIN = 0.20
    WEAK_C1_PENALTY_MAX = 0.24

    # motor
    MOTOR_CUMULATIVE_WEIGHT = 0.70
    MOTOR_RECENT10_WEIGHT = 0.30
    DAY1_MOTOR_FACTOR = 0.70

    # C4 -> C5 second linkage (0.14 candidate -> 0.09 fixed)
    C4_C5_SECOND_LINK = 0.09

    # tickets
    PROB_FLOOR = 1e-8
    HEDGE_TOP_HEAD_MIN = 0.45
    HEDGE_TOP_HEAD_SLOTS_MIN = 5
    HEDGE_OUTER_HEAD_MAX = 0.15
    HEDGE_MIN_RELATIVE_SCORE = 0.08

    def __init__(self, db_path, config_path):
        super().__init__(db_path, config_path)
        self._second_conditional_cache: Dict[int, Dict[int, Tuple[float, int]]] = {}

    # ------------------------------------------------------------------
    # Motor: cumulative 70% + recent10 30%
    # ------------------------------------------------------------------
    def _motor_signal(self, boat: Dict[str, Any]):
        top2_cum = base._rate01(boat.get("motor_top2_rate"))
        top3_cum = base._rate01(boat.get("motor_top3_rate"))
        recent2 = recent3 = None
        row = None

        motor_no = boat.get("motor_no")
        if motor_no is not None:
            row = self.conn.execute(
                """
                SELECT top2_rate_total, top3_rate_total,
                       win_rate_recent10, second_rate_recent10, top3_rate_recent10,
                       starts_recent10, date
                FROM motor_recent
                WHERE motor_no=?
                ORDER BY date DESC
                LIMIT 1
                """,
                (int(motor_no),),
            ).fetchone()

        if row:
            if top2_cum is None:
                top2_cum = base._rate01(row["top2_rate_total"])
            if top3_cum is None:
                top3_cum = base._rate01(row["top3_rate_total"])
            wr = base._rate01(row["win_rate_recent10"])
            sr = base._rate01(row["second_rate_recent10"])
            recent2 = (wr or 0.0) + (sr or 0.0) if (wr is not None or sr is not None) else None
            recent3 = base._rate01(row["top3_rate_recent10"])

        top2 = top2_cum
        top3 = top3_cum
        if top2_cum is not None and recent2 is not None:
            top2 = self.MOTOR_CUMULATIVE_WEIGHT * top2_cum + self.MOTOR_RECENT10_WEIGHT * recent2
        if top3_cum is not None and recent3 is not None:
            top3 = self.MOTOR_CUMULATIVE_WEIGHT * top3_cum + self.MOTOR_RECENT10_WEIGHT * recent3

        cfg = self.cfg["fixed_parameters"]["motor_signal"]
        zs = []
        if top2 is not None:
            zs.append((top2 - cfg["center_top2"]) / cfg["scale_top2"])
        if top3 is not None:
            zs.append((top3 - cfg["center_top3"]) / cfg["scale_top3"])
        z = base._clip(base._mean(zs), -cfg["cap_z"], cfg["cap_z"]) if zs else 0.0

        return z, {
            "top2": top2,
            "top3": top3,
            "z": z,
            "cumulative_top2": top2_cum,
            "cumulative_top3": top3_cum,
            "recent10_top2": recent2,
            "recent10_top3": recent3,
            "blend": {"cumulative": self.MOTOR_CUMULATIVE_WEIGHT, "recent10": self.MOTOR_RECENT10_WEIGHT},
        }

    def _event_blend_logs(self, boat, race, stage):
        key = self._event_day_key(race)
        cfg_blend = self.cfg["fixed_parameters"]["event_day_blend"][key]
        motor_z, motor_d = self._motor_signal(boat)
        setsukan_z, setsukan_d = self._setsukan_signal(boat, race)

        motor_factor = self.DAY1_MOTOR_FACTOR if key == "day1" else float(cfg_blend["motor_exhibition"])
        setsukan_factor = float(cfg_blend["setsukan"])
        budgets = self.cfg["fixed_parameters"]["machine_event_budget_log"]

        out = {}
        for pos in base.POSITIONS:
            budget = float(budgets[pos])
            out[pos] = budget * (motor_factor * motor_z + setsukan_factor * setsukan_z)
            out[pos] = base._clip(out[pos], -budget, budget)

        return out, {
            "day_key": key,
            "blend": {"motor_exhibition": motor_factor, "setsukan": setsukan_factor},
            "motor": motor_d,
            "setsukan": setsukan_d,
            "logs": out,
            "v12_day1_motor_damped": key == "day1",
        }

    # ------------------------------------------------------------------
    # Attack-defense
    # ------------------------------------------------------------------
    def _attack_defense(self, states, race):
        by_course = {b["actual_course"]: b for b in race["boats"]}
        c1 = by_course.get(1)
        if not c1:
            return {"matches": [], "c1_penalty_log": 0.0, "weak_multi_gate": False}

        defense = self._input_attack_defense(c1)
        matches = []
        total = 0.0
        core_attackers = 0

        for c in (2, 3, 4, 5, 6):
            b = by_course.get(c)
            if not b:
                continue
            inp = self._input_attack_defense(b)
            dbp = self._db_attack_profile(b)
            attacks = {k: max(inp[k], dbp[k]) for k in ("sashi", "makuri", "makurizashi")}
            raw_score = (
                defense["sashi_allowed"] * attacks["sashi"]
                + defense["makuri_allowed"] * attacks["makuri"]
                + defense["makurizashi_allowed"] * attacks["makurizashi"]
            )
            weight = self.OUTER_ATTACK_WEIGHT[c]
            score = raw_score * weight

            if c <= 4 and raw_score >= self.WEAK_C1_ATTACK_SCORE_MIN:
                core_attackers += 1

            if score > 0 and (c <= 4 or raw_score >= self.OUTER_ATTACK_MIN_SCORE):
                matches.append({
                    "course": c,
                    "lane": b["lane"],
                    "score": score,
                    "raw_score": raw_score,
                    "course_weight": weight,
                    "attacks": attacks,
                })
                total += score

        cfg = self.cfg["fixed_parameters"]["attack_defense"]
        base_penalty = 0.0
        if matches:
            base_penalty = min(
                float(cfg["max_head_log_shift"]),
                float(cfg["match_weight"]) * total
                + float(cfg["multiple_attacker_bonus"]) * max(0, len(matches) - 1),
            )

        vulnerability = (
            defense["sashi_allowed"]
            + defense["makuri_allowed"]
            + defense["makurizashi_allowed"]
        )
        weak_multi_gate = (
            defense["escape"] < self.WEAK_C1_ESCAPE_MAX
            and vulnerability >= self.WEAK_C1_VULNERABILITY_MIN
            and core_attackers >= self.WEAK_C1_CORE_ATTACKERS_MIN
        )

        penalty = base_penalty
        if weak_multi_gate:
            penalty = max(penalty, self.WEAK_C1_PENALTY_MIN)
            penalty = min(penalty, self.WEAK_C1_PENALTY_MAX)

        if matches and penalty > 0:
            state_by_lane = {s.lane: s for s in states}
            state_by_lane[c1["lane"]].logs["win"] -= penalty
            denom = sum(m["score"] for m in matches) or 1.0
            for m in matches:
                state_by_lane[m["lane"]].logs["win"] += penalty * 0.85 * m["score"] / denom

        return {
            "defense": defense,
            "matches": matches,
            "c1_penalty_log": penalty,
            "base_penalty_log": base_penalty,
            "weak_multi_gate": weak_multi_gate,
            "vulnerability_sum": vulnerability,
            "core_attackers": core_attackers,
        }

    # ------------------------------------------------------------------
    # Slit: add outer-attack detection for ticket scenario only.
    # Raw exhibition ST is still not a direct large probability coefficient.
    # ------------------------------------------------------------------
    def _slit_geometry(self, race):
        geom = super()._slit_geometry(race)
        by_course = {b["actual_course"]: b for b in race["boats"]}
        st = {c: base._safe_float(b.get("exhibition_st")) for c, b in by_course.items()}
        margin = float(self.cfg["fixed_parameters"]["slit_geometry"]["attack_margin"])
        outer_attack = []
        for c in (5, 6):
            x, prev = st.get(c), st.get(c - 1)
            if x is not None and prev is not None and x <= prev - margin:
                outer_attack.append(c)
        geom["outer_attack_courses"] = outer_attack
        return geom

    # ------------------------------------------------------------------
    # Conditional order
    # ------------------------------------------------------------------
    def _conditional_second_db(self, first_course: int):
        if first_course in self._second_conditional_cache:
            return self._second_conditional_cache[first_course]
        rows = self.conn.execute(
            """
            WITH races AS (
              SELECT date, race_no,
                     MAX(CASE WHEN finish=1 THEN actual_course END) c1,
                     MAX(CASE WHEN finish=2 THEN actual_course END) c2
              FROM race_history
              WHERE date >= ?
              GROUP BY date, race_no
            )
            SELECT c2 second_course, COUNT(*) occurrences
            FROM races
            WHERE c1=? AND c2 IS NOT NULL
            GROUP BY c2
            """,
            (self.current_start, first_course),
        ).fetchall()
        counts = {int(r["second_course"]): int(r["occurrences"]) for r in rows}
        den = sum(counts.values())
        result = {c: (n / den, den) for c, n in counts.items()} if den else {}
        self._second_conditional_cache[first_course] = result
        return result

    def _second_blend(self, n: int) -> float:
        if n >= self.SECOND_N_STRONG:
            return self.SECOND_BLEND_STRONG
        if n >= self.SECOND_N_MEDIUM:
            return self.SECOND_BLEND_MEDIUM
        if n >= self.SECOND_N_WEAK:
            return self.SECOND_BLEND_WEAK
        return 0.0

    def _follow_suitable(self, lane: int, probs: Dict[int, Dict[str, float]]) -> bool:
        """Globalで既にmotor/local/exhibitionを反映済みなので二重加点しない。
        ここでは追走候補の2/3着基礎力が全艇中央値以上かだけをinteraction gateに使う。
        """
        vals = sorted((p["second"] + p["third"]) for p in probs.values())
        median = (vals[2] + vals[3]) / 2.0
        return probs[lane]["second"] + probs[lane]["third"] >= median

    def _conditional_ticket_probs(self, probs, states, scenarios, live):
        course_to_lane, lane_to_course = self._course_lane_maps(states)
        scen_by_head_course = {
            1: "1_escape_2_sashi",
            2: "2_sashi",
            3: "3_attack",
            4: "4_attack",
            5: "outer_chaos",
            6: "outer_chaos",
        }
        cfg = self.cfg["fixed_parameters"]["conditional_order"]
        geom = live.get("slit_geometry", {}) if live else {}
        attack_courses = geom.get("attack_courses", [])
        outer_attack_courses = geom.get("outer_attack_courses", [])

        out = []
        for a, b, c in itertools.permutations(sorted(probs), 3):
            ca, cb, cc = lane_to_course[a], lane_to_course[b], lane_to_course[c]
            p1 = probs[a]["win"]

            second_raw = {lane: probs[lane]["second"] for lane in probs if lane != a}
            base_second = base._normalize_dict(second_raw)

            hist_db = self._conditional_second_db(ca)
            hist_raw = {
                lane: hist_db.get(lane_to_course[lane], (0.0, 0))[0]
                for lane in second_raw
            }
            n2 = next(iter(hist_db.values()))[1] if hist_db else 0
            blend2 = self._second_blend(n2)

            if sum(hist_raw.values()) > 0 and blend2 > 0:
                hist_second = base._normalize_dict(hist_raw)
                second_cond = base._normalize_dict({
                    lane: (1.0 - blend2) * base_second[lane] + blend2 * hist_second[lane]
                    for lane in base_second
                })
            else:
                second_cond = base_second

            # C4 attack -> C5 follow-through.
            # v1.1の単純0.14を使わず、C4が実際に攻め位置かつC5の基礎2/3着力がある時だけ0.09。
            if ca == 4 and 4 in attack_courses:
                lane5 = course_to_lane.get(5)
                link5 = geom.get("outer_link", {}).get(5, 0.0)
                if lane5 in second_cond and link5 > 0 and self._follow_suitable(lane5, probs):
                    second_cond[lane5] *= math.exp(self.C4_C5_SECOND_LINK * link5)
                    second_cond = base._normalize_dict(second_cond)

            p2 = second_cond[b]

            third_raw = {lane: probs[lane]["third"] for lane in probs if lane not in (a, b)}
            db = self._conditional_order_db(ca, cb)
            for lane in list(third_raw):
                tc = lane_to_course[lane]
                if tc in db:
                    q, n = db[tc]
                    if n >= cfg["n_strong"]:
                        blend = cfg["blend_strong"]
                    elif n >= cfg["n_medium"]:
                        blend = cfg["blend_medium"]
                    elif n >= cfg["n_weak"]:
                        blend = cfg["blend_weak"]
                    else:
                        blend = cfg["blend_none"]
                    third_raw[lane] = (1.0 - blend) * third_raw[lane] + blend * q

                scen = scen_by_head_course.get(ca, "outer_chaos")
                third_raw[lane] *= self._branch_bonus(ca, cb, tc, scen, live)

            third_cond = base._normalize_dict(third_raw)
            p3 = third_cond[c]
            scen = scen_by_head_course.get(ca, "outer_chaos")

            out.append({
                "ticket": f"{a}-{b}-{c}",
                "lanes": (a, b, c),
                "courses": (ca, cb, cc),
                "score": p1 * p2 * p3,
                "p_first": p1,
                "p_second_given_first": p2,
                "p_third_given_first_second": p3,
                "scenario": scen,
                "second_order_blend": blend2,
                "outer_attack_courses": tuple(outer_attack_courses),
            })

        out.sort(key=lambda x: x["score"], reverse=True)
        return out

    # ------------------------------------------------------------------
    # Ticket generation
    # ------------------------------------------------------------------
    @staticmethod
    def _base_head_slots(p: float) -> int:
        # 7R/8R/6Rで固定した切り分け
        if p >= 0.55:
            return 6
        if p >= 0.45:
            return 5
        if p >= 0.35:
            return 4
        if p >= 0.25:
            return 3
        if p >= 0.20:
            return 2
        if p >= 0.08:
            return 1
        return 0

    def _head_slot_targets(self, probs, joint):
        slots = {lane: self._base_head_slots(probs[lane]["win"]) for lane in probs}

        # If base exceeds 10, remove from weakest heads first, never below 0.
        while sum(slots.values()) > 10:
            candidates = [l for l in slots if slots[l] > 0]
            lane = min(candidates, key=lambda l: (probs[l]["win"], slots[l]))
            slots[lane] -= 1

        # 20-24.9% head gets one scenario-diversity bonus first.
        # This reproduces: 6R C4 20.32% -> 3 tickets, 8R C2 20.8% -> 3 tickets.
        if sum(slots.values()) < 10:
            mid_heads = [
                l for l in probs
                if 0.20 <= probs[l]["win"] < 0.25
                and len([r for r in joint if r["lanes"][0] == l and r["score"] > self.PROB_FLOOR]) >= 3
            ]
            for lane in sorted(mid_heads, key=lambda l: probs[l]["win"], reverse=True):
                if sum(slots.values()) >= 10:
                    break
                slots[lane] += 1

        # Very strong head gets one extra if room remains.
        if sum(slots.values()) < 10:
            top = max(probs, key=lambda l: probs[l]["win"])
            if probs[top]["win"] >= 0.55:
                slots[top] += 1

        # Remaining slots: best available head-scenario score wins.
        while sum(slots.values()) < 10:
            best = None
            for lane in probs:
                rows = [r for r in joint if r["lanes"][0] == lane and r["score"] > self.PROB_FLOOR]
                idx = slots[lane]
                if idx < len(rows):
                    cand = rows[idx]
                    if best is None or cand["score"] > best[0]:
                        best = (cand["score"], lane)
            if best is None:
                break
            slots[best[1]] += 1

        return slots

    def _find_hedge_candidate(self, chosen, joint, probs, slots):
        top_head = max(probs, key=lambda l: probs[l]["win"])
        if probs[top_head]["win"] < self.HEDGE_TOP_HEAD_MIN:
            return None
        if slots.get(top_head, 0) < self.HEDGE_TOP_HEAD_SLOTS_MIN:
            return None

        outer = set()
        for r in joint:
            outer.update(r.get("outer_attack_courses", ()))
        if not outer:
            return None

        # Strongest detected outer attacker, but it must not itself be a strong head.
        lane_by_course = {}
        for r in joint[:1]:
            for lane, course in zip(r["lanes"], r["courses"]):
                lane_by_course[course] = lane

        eligible_outer = []
        for oc in sorted(outer, reverse=True):
            olane = lane_by_course.get(oc)
            if olane is not None and probs[olane]["win"] < self.HEDGE_OUTER_HEAD_MAX:
                eligible_outer.append(oc)
        if not eligible_outer:
            return None

        oc = eligible_outer[0]
        inner_pair = (oc - 1, oc - 2)  # 6攻め=>5/4, 5攻め=>4/3

        candidates = [
            r for r in joint
            if r["lanes"][0] == top_head
            and set(r["courses"][1:]) == set(inner_pair)
        ]
        if not candidates:
            return None
        candidate = max(candidates, key=lambda r: r["score"])

        same_head_selected = [r for r in chosen if r["lanes"][0] == top_head]
        if not same_head_selected:
            return None
        weakest = min(same_head_selected, key=lambda r: r["score"])
        if candidate["score"] < self.HEDGE_MIN_RELATIVE_SCORE * weakest["score"]:
            return None

        return candidate, weakest, oc

    def _generate_tickets(self, joint: List[Dict[str, Any]], probs: Dict[int, Dict[str, float]]):
        positive = [r for r in joint if r["score"] > self.PROB_FLOOR]
        if len(positive) < 10:
            raise ValueError("fewer than 10 positive-probability trifecta candidates")

        slots = self._head_slot_targets(probs, positive)
        chosen = []
        used = set()

        # Within each head, use scenario/joint score ranking.
        for lane, n in sorted(slots.items(), key=lambda kv: probs[kv[0]]["win"], reverse=True):
            rows = [r for r in positive if r["lanes"][0] == lane]
            for r in rows[:n]:
                if r["ticket"] not in used:
                    chosen.append(r)
                    used.add(r["ticket"])

        # Exactly 10
        for r in positive:
            if len(chosen) >= 10:
                break
            if r["ticket"] not in used:
                chosen.append(r)
                used.add(r["ticket"])
        chosen = chosen[:10]

        # Strong-axis outer-attack failure hedge: maximum one ticket.
        hedge = self._find_hedge_candidate(chosen, positive, probs, slots)
        hedge_meta = None
        if hedge:
            candidate, replace_row, outer_course = hedge
            if candidate["ticket"] not in used:
                idx = chosen.index(replace_row)
                used.remove(replace_row["ticket"])
                chosen[idx] = candidate
                used.add(candidate["ticket"])
                hedge_meta = {
                    "enabled": True,
                    "ticket": candidate["ticket"],
                    "replaced": replace_row["ticket"],
                    "outer_attack_course": outer_course,
                    "reason": "strong_head_outer_attack_fails_inner_residual",
                }

        # Display roles only; selection is not quota-based by these labels.
        chosen.sort(key=lambda r: (-probs[r["lanes"][0]]["win"], -r["score"]))
        main = chosen[:6]
        deviation = chosen[6:8]
        upset = chosen[8:10]

        fmt = lambda xs: [r["ticket"] for r in xs]
        return {
            "main": fmt(main),
            "deviation": fmt(deviation),
            "upset": fmt(upset),
            "head_slots": {str(k): v for k, v in sorted(slots.items()) if v > 0},
            "scenario_hedge": hedge_meta or {"enabled": False},
            "ranked_top20": [
                {k: r[k] for k in (
                    "ticket", "score", "p_first",
                    "p_second_given_first", "p_third_given_first_second", "scenario"
                )}
                for r in positive[:20]
            ],
        }

    def predict(self, race, stage="preliminary"):
        out = super().predict(race, stage)
        out["engine_version"] = self.ENGINE_VERSION
        out["parameter_version"] = self.PARAMETER_VERSION
        out["rules"]["same_day_trend_used"] = False
        out["rules"]["motor_blend"] = {
            "cumulative": self.MOTOR_CUMULATIVE_WEIGHT,
            "recent10": self.MOTOR_RECENT10_WEIGHT,
            "day1_motor_factor": self.DAY1_MOTOR_FACTOR,
        }
        out["rules"]["weak_c1_multi_attack_gate"] = {
            "escape_lt": self.WEAK_C1_ESCAPE_MAX,
            "vulnerability_gte": self.WEAK_C1_VULNERABILITY_MIN,
            "core_attackers_gte": self.WEAK_C1_CORE_ATTACKERS_MIN,
            "penalty_log_min": self.WEAK_C1_PENALTY_MIN,
            "penalty_log_max": self.WEAK_C1_PENALTY_MAX,
        }
        out["rules"]["ticket_structure"] = {
            "selection": "head_probability_slots_then_scenario_joint",
            "count": 10,
            "strong_axis_outer_attack_hedge_max": 1,
        }
        return out


# compatibility alias
BiwakoPredictionEngine = BiwakoPredictionEngineV12
