#!/usr/bin/env python3
from pathlib import Path
import argparse
import json
import math

import pandas as pd

from heiwajima_master_loader import MasterLoader
from heiwajima_water_engine import water_features
from heiwajima_scenario_engine import evaluate_scenarios, scenario_position_adjustments
from heiwajima_ticket_engine import generate_tickets
from heiwajima_sab_engine import judge_sab
from heiwajima_race_baseline import race_specific_first_baseline
from heiwajima_slit_engine import calculate_slit_adjustments
from heiwajima_class_motor_engine import class_motor_multipliers
from heiwajima_five_head_engine import five_head_scenario_adjustment
from heiwajima_v2_5_adjustments import (
    course_local_interaction,
    motor_recent_adjustment,
    original_exhibition_composite,
    outer_break_adjustment,
)

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def softmax(vals):
    m = max(vals)
    ex = [math.exp(v - m) for v in vals]
    total = sum(ex) or 1.0
    return [value / total for value in ex]


def num(value, default=0.0):
    try:
        if value is None or value == "" or value == "-":
            return default
        parsed = float(value)
        return default if math.isnan(parsed) else parsed
    except (TypeError, ValueError):
        return default


def row1(frame):
    return {} if frame.empty else frame.iloc[0].to_dict()


def rate(data, key, default=0.0):
    value = num(data.get(key), default)
    return value / 100.0 if abs(value) > 1.0 else value


def clip(value, cap):
    return max(-cap, min(cap, value))


def _day_bucket(data):
    raw = data.get("event_day_no") or data.get("race", {}).get("event_day_no") or 1
    try:
        day_no = int(str(raw).replace("日目", ""))
    except (TypeError, ValueError):
        day_no = 1
    final = bool(data.get("is_final_day") or data.get("race", {}).get("is_final_day"))
    if final or day_no >= 5:
        return "late"
    if day_no >= 3:
        return "middle"
    return "early"


def _inline_kimarite(boat):
    inline = boat.get("kimarite") or {}
    if not isinstance(inline, dict):
        return {}
    starts = int(num(inline.get("starts"), 0))
    return inline if starts > 0 else {}


def _kimarite_source(boat, sqlite_row):
    inline = _inline_kimarite(boat)
    if inline:
        return inline, "boaters"
    if sqlite_row:
        return sqlite_row, "sqlite"
    return {}, "missing"


def _kimarite_adjustments(course, source, source_type, cap):
    if not source:
        return 0.0, 0.0, 0.0

    if source_type == "boaters":
        starts = max(1.0, num(source.get("starts"), 1.0))
        reliability = min(1.0, math.sqrt(starts / 30.0))

        if course == 1:
            escape = rate(source, "escape_rate", 0.0)
            sashare = rate(source, "sashare_rate", 0.0)
            makurare = rate(source, "makurare_rate", 0.0)
            makurare_zashi = rate(source, "makurare_zashi_rate", 0.0)
            vulnerability = sashare + makurare + makurare_zashi
            win = clip((escape - 0.48) * 0.12 - vulnerability * 0.07, cap) * reliability
            second = clip(vulnerability * 0.035, cap * 0.55) * reliability
            third = clip(vulnerability * 0.025, cap * 0.45) * reliability
            return win, second, third

        sashi = rate(source, "sashi_rate", 0.0)
        makuri = rate(source, "makuri_rate", 0.0)
        makuri_sashi = rate(source, "makuri_sashi_rate", 0.0)
        nigashi = rate(source, "nigashi_rate", 0.0)

        if course == 2:
            attack = sashi * 0.13 + makuri * 0.07 + makuri_sashi * 0.05 - nigashi * 0.025
        elif course in (3, 4):
            attack = makuri * 0.10 + makuri_sashi * 0.09 + sashi * 0.035 - nigashi * 0.015
        else:
            attack = makuri_sashi * 0.08 + makuri * 0.06 + sashi * 0.04 - nigashi * 0.01

        win = clip(attack, cap) * reliability
        second = clip((sashi + makuri_sashi) * 0.035, cap * 0.65) * reliability
        third = clip((sashi + makuri + makuri_sashi) * 0.025, cap * 0.60) * reliability
        return win, second, third

    if course == 2:
        win = clip(rate(source, "sashi_rate_in_wins", 0.0) * 0.10, cap)
    elif course in (3, 4):
        win = clip(
            (rate(source, "makuri_rate_in_wins", 0.0) + rate(source, "makuri_zashi_rate_in_wins", 0.0)) * 0.065,
            cap,
        )
    elif course in (5, 6):
        win = clip(
            (rate(source, "makuri_rate_in_wins", 0.0) + rate(source, "makuri_zashi_rate_in_wins", 0.0)) * 0.045,
            cap * 0.75,
        )
    else:
        win = 0.0
    return win, win * 0.30, win * 0.22



def _boaters_profile(boat):
    inline = _inline_kimarite(boat)
    if not inline:
        return {}
    starts = max(1.0, num(inline.get("starts"), 1.0))
    reliability = min(1.0, math.sqrt(starts / 30.0))
    profile = {"reliability": reliability}
    for key in (
        "escape_rate", "sashare_rate", "makurare_rate", "makurare_zashi_rate",
        "nigashi_rate", "sashi_rate", "makuri_rate", "makuri_sashi_rate",
    ):
        if key in inline:
            profile[key] = rate(inline, key, 0.0)
    return profile


def _apply_kimarite_linkage(records, boats, caps):
    """決まり手を頭率だけでなく、攻め艇の外側連動へ戻す。

    結果は参照せず、当日取得した直近6か月・進入変更なしの決まり手率のみを使う。
    """
    by_lane = {int(b.get("boat_no") or b.get("lane")): b for b in boats}
    rec = {int(r["boat_no"]): r for r in records}
    profiles = {lane: _boaters_profile(boat) for lane, boat in by_lane.items()}

    p1 = profiles.get(1, {})
    if p1:
        escape = p1.get("escape_rate", 0.0)
        vulnerability = (
            p1.get("sashare_rate", 0.0)
            + p1.get("makurare_rate", 0.0)
            + p1.get("makurare_zashi_rate", 0.0)
        )
        rel = p1.get("reliability", 0.0)
        # 逃げ40%未満、またはまくられ系35%以上なら1逃げ固定を解除。
        weakness = max(0.0, (0.40 - escape) * 0.50) + max(0.0, vulnerability - 0.35) * 0.22
        penalty = clip(weakness * rel, caps["kimarite_logit"] * 0.85)
        if penalty > 0:
            rec[1]["win_score"] -= penalty
            rec[1]["second_score"] += penalty * 0.32
            rec[1]["third_score"] += penalty * 0.20
            rec[1]["reason_log"].append({"code": "kimarite_escape_risk", "delta": round(-penalty, 4)})

    # 3・4の攻めは、その外側4・5・6／5・6へ2着・3着連動させる。
    for lane in (3, 4, 5):
        profile = profiles.get(lane, {})
        if not profile:
            continue
        rel = profile.get("reliability", 0.0)
        attack = (
            profile.get("makuri_rate", 0.0) * 0.55
            + profile.get("makuri_sashi_rate", 0.0) * 0.70
            + profile.get("sashi_rate", 0.0) * 0.20
        ) * rel
        if attack <= 0:
            continue
        head_boost = clip(attack * 0.16, caps["kimarite_logit"] * 0.72)
        rec[lane]["win_score"] += head_boost
        rec[lane]["reason_log"].append({"code": "kimarite_attack_head", "delta": round(head_boost, 4)})

        followers = [x for x in range(lane + 1, 7)]
        for offset, follower in enumerate(followers):
            decay = max(0.35, 1.0 - offset * 0.22)
            second_boost = clip(attack * 0.070 * decay, caps["kimarite_logit"] * 0.40)
            third_boost = clip(attack * 0.095 * decay, caps["kimarite_logit"] * 0.52)
            rec[follower]["second_score"] += second_boost
            rec[follower]["third_score"] += third_boost
            rec[follower]["reason_log"].append({
                "code": f"kimarite_link_from_{lane}",
                "delta": round(second_boost + third_boost, 4),
            })

    # 5・6は頭への過剰加点を避け、主に外連動・連下へ使う。
    for lane in (5, 6):
        profile = profiles.get(lane, {})
        if not profile:
            continue
        rel = profile.get("reliability", 0.0)
        outer = (
            profile.get("makuri_sashi_rate", 0.0) * 0.70
            + profile.get("makuri_rate", 0.0) * 0.35
            + profile.get("sashi_rate", 0.0) * 0.20
        ) * rel
        rec[lane]["second_score"] += clip(outer * 0.060, caps["kimarite_logit"] * 0.34)
        rec[lane]["third_score"] += clip(outer * 0.105, caps["kimarite_logit"] * 0.56)


def _apply_sab_guardrails(sab, records, scenarios, completeness, stage):
    guarded = dict(sab or {})
    grade = str(guarded.get("grade") or "B")
    confidence = float(guarded.get("confidence") or 0.0)

    win_sorted = sorted((float(r["win_prob"]) for r in records), reverse=True)
    axis_gap = (win_sorted[0] - win_sorted[1]) if len(win_sorted) >= 2 else 0.0
    top_scenario = float((scenarios[0] if scenarios else {}).get("probability") or 0.0)
    kimarite_ok = int(completeness.get("kimarite_reflected") or 0) >= 6
    course_ok = int(completeness.get("player_course_reflected") or 0) >= 5
    realtime_ok = stage == "final"

    reasons = list(guarded.get("guardrail_reasons") or [])
    if grade == "S" and not realtime_ok:
        grade = "A"
        reasons.append("pre_stage_cannot_be_s")
    if grade == "S" and not kimarite_ok:
        grade = "A"
        reasons.append("kimarite_coverage_below_6")
    if grade == "S" and not course_ok:
        grade = "A"
        reasons.append("player_course_coverage_below_5")
    if grade == "S" and (axis_gap < 0.12 or top_scenario < 0.28):
        grade = "A"
        reasons.append("axis_or_scenario_not_clear")

    if not realtime_ok:
        confidence = min(confidence, 84.9)
    if not kimarite_ok or not course_ok:
        confidence = min(confidence, 79.9)

    guarded["grade"] = grade
    guarded["confidence"] = round(confidence, 1)
    guarded["guardrail_reasons"] = sorted(set(reasons))
    guarded["ticket_count_used"] = False
    return guarded

def calculate(input_data, loader=None):
    loader = loader or MasterLoader()
    stage = input_data.get("stage", "pre")
    boats = input_data.get("boats") or input_data.get("entries") or []
    live_context = input_data.get("live") or {}
    slit_adjustments = calculate_slit_adjustments(boats, live_context)
    five_head = five_head_scenario_adjustment(boats, live_context)
    if len(boats) != 6:
        raise ValueError("boats/entries must contain exactly 6 boats")

    actual_map = {
        int(b.get("boat_no") or b.get("lane")): int(
            b.get("actual_course")
            or b.get("planned_entry_course")
            or b.get("entry_course")
            or b.get("boat_no")
            or b.get("lane")
        )
        for b in boats
    }
    entry_changed = any(k != v for k, v in actual_map.items())

    water = water_features(input_data)
    bucket = _day_bucket(input_data)
    mult = CONFIG["day_stage"][bucket]
    caps = CONFIG["caps"]
    course_base = loader.table("course_baseline")
    global_first_rates = [
        max(0.01, rate(row1(course_base[course_base["course"].astype(str) == str(course)]), "first_rate",
                       [0.4525, 0.1697, 0.1496, 0.1269, 0.0758, 0.0438][course - 1]))
        for course in range(1, 7)
    ]
    race_base = race_specific_first_baseline(input_data, global_first_rates)
    water["five_head_scenario_bonus"] = float(five_head.get("scenario_bonus", 0.0))
    local_df = loader.table("player_local_stats")

    records = []
    missing = []
    player_course_count = 0
    player_lane_count = 0
    local_st_count = 0
    boaters_kimarite_count = 0
    sqlite_kimarite_count = 0

    for b in boats:
        boat = int(b.get("boat_no") or b.get("lane"))
        reg = str(b.get("reg_no") or b.get("player_id"))
        course = actual_map[boat]

        pc = row1(loader.player_course(reg, course))
        lane = row1(loader.player_lane(reg, boat))
        sqlite_pk = row1(loader.player_kimarite(reg, course))

        local = {}
        try:
            normalized_reg = int(float(reg))
        except (TypeError, ValueError):
            normalized_reg = None
        if normalized_reg is not None and not local_df.empty:
            local_reg = pd.to_numeric(local_df["reg_no"], errors="coerce")
            local = row1(local_df[local_reg == normalized_reg])

        base = row1(course_base[course_base["course"].astype(str) == str(course)])

        if pc:
            player_course_count += 1
        else:
            missing.append("player_course_missing")
        if lane:
            player_lane_count += 1
        else:
            missing.append("player_lane_missing")
        if local:
            local_st_count += 1

        bw = max(0.01, float(race_base["first_rates"][course - 1]))
        bs = max(0.01, rate(base, "second_rate", [0.1931, 0.2273, 0.1914, 0.1795, 0.1284, 0.0977][course - 1]))
        bt = max(0.01, rate(base, "third_rate", [0.1158, 0.1976, 0.1844, 0.1803, 0.1659, 0.1726][course - 1]))

        sw, ss, st = math.log(bw), math.log(bs), math.log(bt)
        reasons = []

        if pc:
            rel = {"A": 1.0, "B": 0.72, "C": 0.45}.get(str(pc.get("reliability")), 0.25)
            delta = rate(pc, "win_rate", bw) - bw
            top3d = rate(pc, "top3_vs_course_avg", 0.0)
            cw = clip(delta * 0.50 + top3d * 0.14, caps["player_course_logit"]) * rel
            sw += cw
            ss += clip(top3d * 0.13, caps["player_course_logit"] * 0.65) * rel
            st += clip(top3d * 0.12, caps["player_course_logit"] * 0.55) * rel
            if abs(cw) > 0.015:
                reasons.append({"code": "player_course", "delta": round(cw, 4)})

        if lane and not entry_changed:
            lane_delta = rate(lane, "top3_rate", 0.0) - (rate(pc, "top3_rate", 0.0) if pc else 0.0)
            st += clip(lane_delta * 0.10, caps["lane_logit"])

        if local:
            sw += clip((rate(local, "win_rate", 0.0) - 0.16) * 0.18, caps["local_logit"])

        local_x = course_local_interaction(b, course)
        sw += float(local_x["win"])
        ss += float(local_x["second"])
        st += float(local_x["third"])
        if abs(float(local_x["win"])) >= 0.01:
            reasons.append({"code":"course_local_interaction","delta":round(float(local_x["win"]),4)})

        pk, pk_source = _kimarite_source(b, sqlite_pk)
        kw, ks, kt = _kimarite_adjustments(course, pk, pk_source, caps["kimarite_logit"])
        sw += kw
        ss += ks
        st += kt

        if pk_source == "boaters":
            boaters_kimarite_count += 1
        elif pk_source == "sqlite":
            sqlite_kimarite_count += 1
        else:
            missing.append("kimarite_missing")

        if abs(kw) > 0.005 or abs(ks) > 0.005 or abs(kt) > 0.005:
            reasons.append({"code": f"kimarite_{pk_source}", "delta": round(kw, 4)})

        season = b.get("season") or b.get("season_form") or {}
        motor = b.get("motor") or {}
        ex = b.get("exhibition") or {}

        form = num(season.get("form_score"), 0.0)
        power = num(motor.get("power_score"), 0.0)
        sx = clip(form * 0.055 * mult["season"], caps["season_logit"])
        motor_mult = class_motor_multipliers(b, course, bucket)
        mx_base = power * 0.045 * mult["motor"]
        mx_win = clip(mx_base * motor_mult["win"], caps["motor_logit"])
        mx_second = clip(mx_base * motor_mult["second"], caps["motor_logit"])
        mx_third = clip(mx_base * motor_mult["third"], caps["motor_logit"])

        sw += sx + mx_win
        ss += sx * 0.80 + mx_second * 0.78
        st += sx * 0.68 + mx_third * 0.65

        mr = motor_recent_adjustment(b, course, bucket)
        sw += float(mr["win"])
        ss += float(mr["second"])
        st += float(mr["third"])
        if abs(float(mr["win"])) >= 0.01:
            reasons.append({"code":"motor_recent10","delta":round(float(mr["win"]),4)})

        wx = 0.0
        if course == 1:
            wx = water["escape_bias"]
        elif course in (3, 4):
            wx = water["center_bias"]
        elif course in (5, 6):
            st += clip(water["outer_bias"], caps["water_logit"])

        water_ratio = 0.20 if water.get("empirical") else 0.35
        water_delta = clip(wx * water_ratio, caps["water_logit"])
        sw += water_delta
        if abs(wx) > 0.01:
            reasons.append({"code":"water_residual","delta":round(water_delta,4)})

        if stage == "final":
            st_alone = clip(-num(ex.get("st_delta"), 0.0) * 0.05, 0.010)
            orig = original_exhibition_composite(ex, mult["live"], caps["live_logit"])
            sw += st_alone + float(orig["win"])
            ss += float(orig["second"])
            st += float(orig["third"])
            if abs(float(orig["win"])) >= 0.01:
                reasons.append({"code":"original_exhibition_4factor","delta":round(float(orig["win"]),4)})

        slit_adj = slit_adjustments.get(boat, {})
        sw += float(slit_adj.get("win", 0.0))
        ss += float(slit_adj.get("second", 0.0))
        st += float(slit_adj.get("third", 0.0))

        if stage == "final":
            outer_break = outer_break_adjustment(b, course, live_context, slit_adj, ex, power, bucket)
            sw += float(outer_break["win"])
            ss += float(outer_break["second"])
            st += float(outer_break["third"])
            if outer_break["signals"]:
                reasons.append({"code":"outer_break","delta":round(float(outer_break["win"]),4),"signals":list(outer_break["signals"])})
        if boat == 5 and five_head.get("active"):
            sw += float(five_head.get("win_delta", 0.0))
            sw += math.log(float(five_head.get("course_multiplier", 1.0)))

        records.append({
            "boat_no": boat,
            "reg_no": reg,
            "lane": boat,
            "actual_course": course,
            "entry_changed": course != boat,
            "win_score": sw,
            "second_score": ss,
            "third_score": st,
            "reason_log": reasons,
            "kimarite_source": pk_source,
        })

    _apply_kimarite_linkage(records, boats, caps)

    for key in ("win", "second", "third"):
        probs = softmax([r[f"{key}_score"] for r in records])
        for r, p in zip(records, probs):
            r[f"{key}_prob"] = p

    scenarios = evaluate_scenarios(records, water)
    pos = scenario_position_adjustments(scenarios)

    for r in records:
        adj = pos[r["boat_no"]]
        r["win_score"] += clip((adj["win"] - 0.10) * 0.55, caps["scenario_logit"])
        r["second_score"] += clip((adj["second"] - 0.08) * 0.48, caps["scenario_logit"])
        r["third_score"] += clip((adj["third"] - 0.07) * 0.44, caps["scenario_logit"])

    for key in ("win", "second", "third"):
        probs = softmax([r[f"{key}_score"] for r in records])
        for r, p in zip(records, probs):
            r[f"{key}_prob"] = round(p, 6)

    for r in records:
        r["top3_prob"] = round(min(1.0, r["win_prob"] + r["second_prob"] + r["third_prob"]), 6)

    if not input_data.get("tide"):
        missing.append("tide_phase_unresolved")
    if stage != "final":
        missing += ["exhibition_pending", "original_exhibition_pending"]
    if any(not (b.get("motor") or {}).get("power_score") for b in boats):
        missing.append("motor_data_missing")

    tickets = generate_tickets(records, scenarios, max_tickets=int(input_data.get("max_tickets", 10)))

    completeness = {
        "master_db_loaded": True,
        "player_course_reflected": player_course_count,
        "player_lane_reflected": player_lane_count,
        "local_st_reflected": local_st_count,
        "boaters_kimarite_reflected": boaters_kimarite_count,
        "sqlite_kimarite_reflected": sqlite_kimarite_count,
        "kimarite_reflected": boaters_kimarite_count + sqlite_kimarite_count,
        "entry_changed": entry_changed,
        "missing_codes": sorted(set(missing)),
    }

    sab = judge_sab(records, scenarios, completeness, len(tickets))
    sab = _apply_sab_guardrails(sab, records, scenarios, completeness, stage)
    top_heads = {b["boat_no"] for b in sorted(records, key=lambda x: x["win_prob"], reverse=True)[:3]}
    exclusions = [
        {
            "boat_no": b["boat_no"],
            "win_probability": b["win_prob"],
            "reason_codes": ["scenario_priority_lower", "relative_head_score"],
        }
        for b in records
        if b["win_prob"] >= 0.10 and b["boat_no"] not in top_heads
    ]

    return {
        "schema_version": "1.7.0",
        "engine_version": CONFIG["engine_version"],
        "venue": "heiwajima",
        "race_date": input_data["race_date"],
        "race_no": input_data["race_no"],
        "stage": stage,
        "day_stage_bucket": bucket,
        "entry_order": None,
        "probabilities": records,
        "scenarios": scenarios,
        "sab": sab,
        "tickets": tickets,
        "head_exclusion_log": exclusions,
        "data_completeness": completeness,
        "odds_used_for_prediction": False,
        "race_specific_baseline": race_base,
        "slit_adjustments": slit_adjustments,
        "five_head_context": five_head,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    output = calculate(data)
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
