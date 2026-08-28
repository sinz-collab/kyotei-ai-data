from __future__ import annotations

import statistics


def _num(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


_ADVANCE = ("advance", "ahead", "front", "前出")
_DENT = ("dent", "behind", "recess", "凹み")


def calculate_slit_adjustments(boats, live):
    live = live or {}
    slit = live.get("slit") or {}
    exhibition_st = live.get("exhibition_st") or {}
    straight_rank = live.get("straight_rank") or {}

    st_values = []
    for lane in range(1, 7):
        value = _num(exhibition_st.get(str(lane), exhibition_st.get(lane)))
        if value is not None:
            st_values.append(value)
    median_st = statistics.median(st_values) if st_values else None

    out = {}
    for boat in boats:
        lane = int(boat.get("boat_no") or boat.get("lane"))
        state = str(slit.get(str(lane), slit.get(lane, "neutral")) or "neutral")
        local_st = _num(boat.get("local_st"))
        avg_st = _num(boat.get("avg_st"))
        ex_st = _num(exhibition_st.get(str(lane), exhibition_st.get(lane)))
        straight = _num(straight_rank.get(str(lane), straight_rank.get(lane)))

        reference_st = local_st if local_st is not None else avg_st
        local_good = reference_st is not None and reference_st <= 0.16
        local_bad = reference_st is not None and reference_st >= 0.19
        straight_good = straight is not None and straight <= 2
        straight_bad = straight is not None and straight >= 5

        adj = {"win":0.0,"second":0.0,"third":0.0,"attack":0.0,"wall":0.0,"reasons":[]}

        if state in _ADVANCE:
            base = {1:0.030,2:0.038,3:0.050,4:0.058,5:0.066,6:0.072}[lane]
            bonus = base
            if local_good:
                bonus += 0.012
            if straight_good:
                bonus += 0.010
            if median_st is not None and ex_st is not None and ex_st <= median_st - 0.045:
                bonus += 0.015
            adj["win"] += bonus
            adj["second"] += min(0.050, bonus * 0.55)
            adj["third"] += min(0.030, bonus * 0.30)
            adj["attack"] += min(0.090, 0.045 + max(0.0, bonus - base))
            adj["reasons"].append(f"start_composite_advance:+{bonus:.3f}")

        if state in _DENT:
            penalty = -0.045
            if median_st is not None and ex_st is not None and ex_st >= median_st + 0.075:
                penalty -= 0.025
            if local_bad:
                penalty -= 0.018
            elif local_good:
                penalty += 0.008
            if straight_bad:
                penalty -= 0.010
            adj["win"] += penalty
            adj["second"] -= min(0.035, abs(penalty) * 0.40)
            adj["third"] -= min(0.020, abs(penalty) * 0.20)
            adj["wall"] -= 0.050
            adj["reasons"].append(f"start_composite_dent:{penalty:.3f}")

        if state not in _ADVANCE + _DENT and median_st is not None and ex_st is not None:
            gap = median_st - ex_st
            if gap >= 0.055:
                adj["win"] += 0.018
                adj["attack"] += 0.020
                adj["reasons"].append("ex_st_relative_fast:+0.018")
            elif gap <= -0.090:
                adj["win"] -= 0.015
                adj["reasons"].append("ex_st_relative_slow:-0.015")

        adj["win"] = max(-0.120, min(0.115, adj["win"]))
        adj["second"] = max(-0.060, min(0.060, adj["second"]))
        adj["third"] = max(-0.040, min(0.040, adj["third"]))
        out[lane] = adj

    for inner in range(1, 6):
        state = str(slit.get(str(inner), slit.get(inner, "neutral")) or "neutral")
        if state not in _DENT:
            continue
        for distance, outer in ((1, inner + 1), (2, inner + 2)):
            if outer not in out or outer > 6:
                continue
            outer_state = str(slit.get(str(outer), slit.get(outer, "neutral")) or "neutral")
            add = 0.026 if distance == 1 else 0.014
            if outer_state in _ADVANCE:
                add += 0.012
            out[outer]["win"] = min(0.135 if outer >= 5 else 0.120, out[outer]["win"] + add)
            out[outer]["attack"] = min(0.120, out[outer]["attack"] + add * 1.4)
            out[outer]["second"] = min(0.070, out[outer]["second"] + add * 0.35)
            out[outer]["third"] = min(0.050, out[outer]["third"] + add * 0.25)
            out[outer]["reasons"].append(f"inside_dent_link_{inner}:+{add:.3f}")

    s1 = str(slit.get("1", slit.get(1, "neutral")) or "neutral")
    s2 = str(slit.get("2", slit.get(2, "neutral")) or "neutral")
    if s1 in _DENT and s2 in _DENT:
        for lane, add in ((3, 0.025), (4, 0.018)):
            if lane in out:
                out[lane]["win"] = min(0.125, out[lane]["win"] + add)
                out[lane]["attack"] = min(0.125, out[lane]["attack"] + add * 1.5)
                out[lane]["reasons"].append(f"double_inner_dent:+{add:.3f}")

    s4 = str(slit.get("4", slit.get(4, "neutral")) or "neutral")
    if s4 in _DENT:
        for lane, add in ((5, 0.025), (6, 0.020)):
            state = str(slit.get(str(lane), slit.get(lane, "neutral")) or "neutral")
            if lane in out and state in _ADVANCE:
                out[lane]["win"] = min(0.140, out[lane]["win"] + add)
                out[lane]["attack"] = min(0.130, out[lane]["attack"] + add * 1.6)
                out[lane]["third"] = min(0.055, out[lane]["third"] + add * 0.30)
                out[lane]["reasons"].append(f"course4_dent_outer_break:+{add:.3f}")

    return out
