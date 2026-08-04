from __future__ import annotations


def _num(value, default=None):
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_slit_adjustments(boats, live):
    """平和島向けスリット複合補正。

    展示ST単独では強補正せず、前出し・凹み・当地ST・直線を複合評価する。
    """
    live = live or {}
    slit = live.get("slit") or {}
    exhibition_st = live.get("exhibition_st") or {}
    straight_rank = live.get("straight_rank") or {}

    out = {}
    for boat in boats:
        lane = int(boat.get("boat_no") or boat.get("lane"))
        state = str(slit.get(str(lane), slit.get(lane, "neutral")) or "neutral")
        local_st = _num(boat.get("local_st"))
        ex_st = _num(exhibition_st.get(str(lane), exhibition_st.get(lane)))
        straight = _num(straight_rank.get(str(lane), straight_rank.get(lane)))

        local_good = local_st is not None and local_st <= 0.16
        local_bad = local_st is not None and local_st >= 0.19
        ex_bad = ex_st is not None and ex_st >= 0.20
        straight_good = straight is not None and straight <= 2

        adj = {
            "win": 0.0,
            "second": 0.0,
            "third": 0.0,
            "attack": 0.0,
            "wall": 0.0,
            "reasons": [],
        }

        if state in ("advance", "ahead", "front", "前出"):
            bonus = 0.02
            if local_good:
                bonus += 0.015
            if straight_good:
                bonus += 0.015
            if lane in (3, 4):
                bonus += 0.01
                adj["attack"] += 0.04

            bonus = min(0.06, bonus)
            adj["win"] += bonus
            adj["second"] += min(0.035, bonus * 0.65)
            adj["reasons"].append(f"slit_advance:+{bonus:.3f}")

        if state in ("dent", "behind", "recess", "凹み"):
            penalty = -0.025 - (0.02 if local_bad else 0.0)
            penalty = max(-0.05, penalty)
            adj["win"] += penalty
            adj["second"] -= 0.015
            adj["wall"] -= 0.03
            adj["reasons"].append(f"slit_dent:{penalty:.3f}")

        if ex_bad:
            if local_good and state not in ("dent", "behind", "recess", "凹み"):
                adj["reasons"].append("bad_ex_st_waived_by_local_st")
            elif state in ("dent", "behind", "recess", "凹み") and local_bad:
                adj["win"] -= 0.02
                adj["second"] -= 0.01
                adj["reasons"].append("bad_ex_st_confirmed:-0.020")
            else:
                adj["win"] -= 0.01
                adj["reasons"].append("bad_ex_st_minor:-0.010")

        adj["win"] = max(-0.06, min(0.06, adj["win"]))
        adj["second"] = max(-0.04, min(0.04, adj["second"]))
        out[lane] = adj

    # 内側凹み時に外隣の攻め艇へ連動加点
    for lane in range(1, 6):
        state = str(slit.get(str(lane), slit.get(lane, "neutral")) or "neutral")
        if state in ("dent", "behind", "recess", "凹み") and lane + 1 in out:
            out[lane + 1]["win"] = min(0.06, out[lane + 1]["win"] + 0.015)
            out[lane + 1]["attack"] = min(0.06, out[lane + 1]["attack"] + 0.03)
            out[lane + 1]["reasons"].append("inside_dent_attack:+0.015")

    return out
