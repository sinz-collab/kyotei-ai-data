from __future__ import annotations
from pathlib import Path
import json
from .master_db import MasterDB
from .probability import ProbabilityEngine
from .scenario import ScenarioEngine
from .tickets import TicketGenerator
from .sab import SabEngine
from .utils import percentage, normalize, safe_float

class OmuraPredictionEngine:
    def __init__(self, master_dir: str | Path, config_path: str | Path):
        self.master = MasterDB(master_dir)
        self.config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        self.probability = ProbabilityEngine(self.master, self.config)
        self.scenario = ScenarioEngine()
        self.ticket = TicketGenerator()
        self.sab = SabEngine(self.config)

    def _validate(self, payload: dict) -> None:
        boats = payload.get("boats", [])
        if len(boats) != 6:
            raise ValueError("boats must contain exactly 6 entries")
        lanes = sorted(int(b["lane"]) for b in boats)
        if lanes != [1, 2, 3, 4, 5, 6]:
            raise ValueError("lane must be unique 1..6")
        entries = [int(b.get("entry_course") or b["lane"]) for b in boats]
        if sorted(entries) != [1, 2, 3, 4, 5, 6]:
            raise ValueError("entry_course must be a permutation of 1..6")

    def _data_quality(self, payload: dict) -> dict:
        required_boat = ("player_id", "grade", "national_win_rate", "local_win_rate", "motor_recent10", "kimarite")
        total = len(required_boat) * 6
        present = sum(1 for b in payload["boats"] for key in required_boat if key in b and b[key] not in (None, "", {}))
        missing = [
            f"lane{b['lane']}.{key}"
            for b in payload["boats"] for key in required_boat
            if key not in b or b[key] in (None, "", {})
        ]
        water = payload.get("water", {})
        water_required = ("tide_direction", "normalized_tide_height", "minutes_from_nearest_extreme")
        total += len(water_required)
        for key in water_required:
            value = water.get(key)
            if value not in (None, "", "unknown"):
                present += 1
            else:
                missing.append(f"water.{key}")
        provenance_penalty = 0.0
        for b in payload["boats"]:
            src = (b.get("motor_recent10") or {}).get("source", "")
            if src not in ("site_recent10", "official_recent10"):
                provenance_penalty += 0.0125
                missing.append(f"lane{b['lane']}.motor_recent10_source")
        completeness = max(0.0, (present / total if total else 0) - provenance_penalty)
        return {"completeness": round(completeness, 4), "missing_fields": missing}

    def _link_attack_scenario_to_probabilities(
        self,
        boats: list[dict],
        probs: dict,
        scenarios: dict,
        water: dict,
    ) -> tuple[dict, dict]:
        """
        Convert a high attack/outer scenario into bounded, boat-specific
        position probability transfers.

        Generic activation signals:
        - attack scenarios are collectively dominant;
        - an outside boat starts >=0.08 ahead of its immediate inside boat;
        - the attacker has at least ordinary local/grade support;
        - near a tide extreme or in wind/wave conditions, the transfer is strengthened.

        This never selects a boat from the result and caps normal win transfer at 5%.
        """
        rows = probs["boats"]
        by_lane = {int(b["lane"]): b for b in boats}
        by_prob = {int(r["lane"]): r for r in rows}
        attack_prob = sum(
            float(scenarios["probabilities"].get(k, 0.0))
            for k in ("makuri", "makurizashi", "outer_upset")
        )
        if attack_prob < 0.52:
            return probs, {"applied": False, "reason": "attack_probability_below_threshold"}

        candidates = []
        grade_score = {"B2": 0, "B1": 1, "A2": 2, "A1": 3}
        for lane in (2, 3, 4, 5):
            boat = by_lane.get(lane, {})
            inside = by_lane.get(lane - 1, {})
            st = safe_float((boat.get("exhibition") or {}).get("st"), 9.0)
            inside_st = safe_float((inside.get("exhibition") or {}).get("st"), 9.0)
            gap = inside_st - st
            if gap < 0.08:
                continue

            local = safe_float(boat.get("local_win_rate"), safe_float(boat.get("national_win_rate"), 0.0))
            grade = grade_score.get(str(boat.get("grade", "B1")), 1)
            original_rank = int((boat.get("exhibition") or {}).get("original_sum_rank", 9) or 9)
            ability_ok = grade >= 2 or local >= 5.3 or original_rank <= 2
            if not ability_ok:
                continue

            score = gap
            score += max(0.0, local - 5.0) * 0.02
            score += 0.025 if grade >= 2 else 0.0
            score += 0.020 if original_rank <= 2 else 0.0
            candidates.append((score, lane, gap))

        if not candidates:
            return probs, {"applied": False, "reason": "no_qualified_attack_boat"}

        _, attacker, slit_gap = max(candidates)
        near = safe_float(water.get("minutes_from_nearest_extreme"), 999)
        wind = safe_float(water.get("wind_speed_mps"), 0)
        wave = safe_float(water.get("wave_height_cm"), 0)

        transfer = 0.025
        if slit_gap >= 0.12:
            transfer += 0.010
        if near <= 30:
            transfer += 0.008
        if wind >= 5 or wave >= 4:
            transfer += 0.007
        transfer = min(0.05, transfer)

        win = list(probs["win"])
        second = list(probs["second"])
        third = list(probs["third"])
        idx = {int(r["lane"]): i for i, r in enumerate(rows)}
        ai = idx[attacker]

        # Donors are the strongest current head candidates other than the attacker,
        # weighted toward boats inside the attacker because they are exposed to attack.
        donors = []
        for lane, i in idx.items():
            if lane == attacker:
                continue
            exposure = 1.35 if lane < attacker else 0.75
            donors.append((i, max(0.001, win[i] * exposure)))
        donor_total = sum(w for _, w in donors)
        actual = min(transfer, 0.12 + sum(win[i] for i, _ in donors) - 0.12)
        for i, weight in donors:
            deduction = actual * weight / donor_total
            win[i] = max(0.001, win[i] - deduction)
        win[ai] += actual
        win = normalize(win)

        # Strong pre-race favorite caught inside shifts from win toward second.
        inside_candidates = [lane for lane in idx if lane < attacker]
        if inside_candidates:
            strongest_inside = max(inside_candidates, key=lambda lane: by_prob[lane]["win"])
            si = idx[strongest_inside]
            move = min(0.025, max(0.0, by_prob[strongest_inside]["win"] - 0.20))
            second[si] += move
            second[ai] = max(0.001, second[ai] - move * 0.35)
            other_second = [i for i in range(len(second)) if i not in (si, ai)]
            if other_second:
                take = move * 0.65 / len(other_second)
                for i in other_second:
                    second[i] = max(0.001, second[i] - take)
            second = normalize(second)

        # Outside followers with strong slit receive third-place linkage, not head inflation.
        followers = []
        attacker_st = safe_float((by_lane[attacker].get("exhibition") or {}).get("st"), 9.0)
        for lane in range(attacker + 1, 7):
            st = safe_float((by_lane.get(lane, {}).get("exhibition") or {}).get("st"), 9.0)
            if st <= attacker_st + 0.02:
                followers.append(lane)
        third_boosts = {}
        for lane in followers[:2]:
            fi = idx[lane]
            boost = 0.018 if lane == attacker + 1 else 0.012
            third[fi] += boost
            third_boosts[str(lane)] = boost
        if third_boosts:
            third = normalize(third)

        for i, row in enumerate(rows):
            row["win"] = win[i]
            row["second"] = second[i]
            row["third"] = third[i]
            row["top3"] = min(1.0, win[i] + second[i] + third[i])
            if int(row["lane"]) == attacker:
                row["signals"] = list(row.get("signals", [])) + ["attack_scenario_linked"]
            if str(row["lane"]) in third_boosts:
                row["signals"] = list(row.get("signals", [])) + ["outside_follow_third_link"]

        probs["win"] = win
        probs["second"] = second
        probs["third"] = third
        return probs, {
            "applied": True,
            "attacker_lane": attacker,
            "slit_gap": round(slit_gap, 3),
            "win_transfer": round(actual, 4),
            "third_boosts": third_boosts,
            "attack_probability_before_link": round(attack_prob, 4),
            "near_extreme_minutes": near,
        }

    def _apply_day1_motor_exhibition_policy(
        self,
        boats: list[dict],
        probs: dict,
        race: dict,
    ) -> tuple[dict, dict]:
        """
        Day-1 only:
        - motor + exhibition + original exhibition agreement may lift win 2-4pt;
        - exhibition alone does not create a head candidate and mainly lifts 2nd/3rd;
        - high-tilt outside exhibition strength is 3rd-place only;
        - exhibition ST alone is never a direct probability input.
        """
        if int(race.get("day_no", 1)) != 1:
            return probs, {"applied": False, "reason": "not_day1"}

        rows = probs["boats"]
        idx = {int(r["lane"]): i for i, r in enumerate(rows)}
        win, second, third = list(probs["win"]), list(probs["second"]), list(probs["third"])
        changes = []

        for boat in boats:
            lane = int(boat["lane"])
            i = idx[lane]
            ex = boat.get("exhibition", {}) or {}
            motor = boat.get("motor_recent10", {}) or {}
            motor_form = safe_float(motor.get("form_score"), 0.0)
            ex_rank = int(ex.get("time_rank", 9) or 9)
            orig_rank = int(ex.get("original_sum_rank", 9) or 9)
            tilt = safe_float(boat.get("tilt", ex.get("tilt", 0.0)), 0.0)
            course = int(boat.get("entry_course") or lane)

            if motor_form >= 0.20 and ex_rank <= 2 and orig_rank <= 2:
                boost = 0.04 if motor_form >= 0.45 else 0.03
                win[i] += boost
                changes.append({"lane": lane, "type": "motor_exhibition_agreement_win", "amount": boost})
            elif ex_rank <= 2 or orig_rank <= 2:
                second[i] += 0.012
                third[i] += 0.018
                changes.append({"lane": lane, "type": "exhibition_only_place", "second": 0.012, "third": 0.018})

            if course >= 5 and tilt >= 1.5 and ex_rank <= 2:
                third[i] += 0.03
                changes.append({"lane": lane, "type": "high_tilt_outside_third_only", "amount": 0.03})

        win = normalize(win)
        second = normalize(second)
        third = normalize(third)
        for i, row in enumerate(rows):
            row["win"], row["second"], row["third"] = win[i], second[i], third[i]
            row["top3"] = min(1.0, win[i] + second[i] + third[i])
        probs["win"], probs["second"], probs["third"] = win, second, third
        return probs, {"applied": bool(changes), "changes": changes}

    def predict(self, payload: dict) -> dict:
        self._validate(payload)
        race = payload["race"]
        water = payload.get("water", {})
        boats = sorted(payload["boats"], key=lambda x: int(x["lane"]))
        probs = self.probability.calculate(boats, race, water)
        probs, day1_policy = self._apply_day1_motor_exhibition_policy(boats, probs, race)
        scenarios = self.scenario.calculate(boats, probs, race, water)
        probs, scenario_link = self._link_attack_scenario_to_probabilities(
            boats, probs, scenarios, water
        )
        scenarios = self.scenario.calculate(boats, probs, race, water)
        scenarios["boat_linkage"] = scenario_link
        scenarios["day1_motor_exhibition_policy"] = day1_policy
        quality = self._data_quality(payload)
        sab = self.sab.calculate(probs, scenarios, quality)
        tickets = self.ticket.generate(probs, scenarios)
        return {
            "engine_version": self.config["version"],
            "venue": "omura",
            "race": race,
            "probabilities": {
                "boats": [{
                    **row,
                    "win_percent": round(row["win"] * 100, 2),
                    "second_percent": round(row["second"] * 100, 2),
                    "third_percent": round(row["third"] * 100, 2),
                    "top3_percent": round(row["top3"] * 100, 2),
                } for row in probs["boats"]],
                "sum_check": {
                    "win": round(sum(probs["win"]), 8),
                    "second": round(sum(probs["second"]), 8),
                    "third": round(sum(probs["third"]), 8),
                }
            },
            "scenario": scenarios,
            "sab": sab,
            "tickets": tickets,
            "data_quality": quality,
            "odds_used_for_prediction": False,
        }
