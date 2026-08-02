from __future__ import annotations

from copy import deepcopy
from typing import Any

LANES = (1, 2, 3, 4, 5, 6)
ENGINE_ID = "wakamatsu_engine_v2.1"
ENGINE_VERSION = "2.1"


def _normalize(values: list[float]) -> list[float]:
    clipped = [max(1e-8, float(v)) for v in values]
    total = sum(clipped)
    return [v / total for v in clipped]


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tide_strength(race_input: dict) -> float:
    tide_type = str(race_input.get("tide_type") or "").strip()
    if not tide_type:
        return 1.0
    minutes = _number(race_input.get("minutes_to_low_tide"))
    if minutes is None:
        return 1.25
    distance = abs(minutes)
    if distance <= 90:
        return 1.35
    if distance <= 180:
        return 1.30
    return 1.25


def _phase(race_input: dict) -> str:
    explicit = str(race_input.get("tide_phase") or "").lower()
    if explicit:
        return explicit
    minutes = _number(race_input.get("minutes_to_low_tide"))
    if minutes is None:
        return "unknown"
    if minutes > 20:
        return "falling"
    if minutes >= -20:
        return "slack"
    return "rising"


def _boat_map(race_input: dict) -> dict[int, dict]:
    result = {}
    for boat in race_input.get("boats") or []:
        lane = int(boat.get("lane", 0))
        if lane in LANES:
            result[lane] = boat
    return result


def _attack_links(race_input: dict) -> list[dict]:
    boats = _boat_map(race_input)
    links: list[dict] = []

    for attack_course, outside_courses in ((3, (4,)), (4, (5, 6))):
        attacker = next(
            (boat for boat in boats.values()
             if int(boat.get("entry_course", 0)) == attack_course),
            None,
        )
        if not attacker:
            continue

        attacker_st = _number(attacker.get("start_time"))
        attacker_ex = float(attacker.get("exhibition_score") or 0.0)
        avg_st = float(attacker.get("avg_st") or 0.18)

        strong = (
            (attacker_st is not None and attacker_st <= 0.12)
            or (attacker_ex >= 0.4 and avg_st <= 0.18)
        )
        if not strong:
            continue

        for outside_course in outside_courses:
            follower = next(
                (boat for boat in boats.values()
                 if int(boat.get("entry_course", 0)) == outside_course),
                None,
            )
            if not follower:
                continue

            follower_st = _number(follower.get("start_time"))
            follower_ex = float(follower.get("exhibition_score") or 0.0)
            follow_ok = (
                (follower_st is not None and follower_st <= 0.14)
                or follower_ex >= 0.2
            )
            if follow_ok:
                links.append({
                    "attacker_lane": int(attacker["lane"]),
                    "follower_lane": int(follower["lane"]),
                    "attack_course": attack_course,
                    "outside_course": outside_course,
                    "strength": 1.0 if outside_course == attack_course + 1 else 0.65,
                })
    return links


def apply_v21_adjustments(result: dict, race_input: dict) -> dict:
    adjusted = deepcopy(result)
    rows = adjusted.get("probabilities") or []
    if len(rows) != 6:
        raise RuntimeError("wakamatsu_v21_probabilities_must_be_6")

    tide_multiplier = _tide_strength(race_input)
    phase = _phase(race_input)
    boats = _boat_map(race_input)
    links = _attack_links(race_input)

    win = [float(row.get("win") or 0.0) for row in rows]
    second = [float(row.get("second") or 0.0) for row in rows]
    third = [float(row.get("third") or 0.0) for row in rows]

    water_extra = min(0.35, max(0.0, tide_multiplier - 1.0))

    if phase == "falling":
        win[0] -= min(0.025, 0.018 * water_extra / 0.35)
        for idx, boost in ((1, 0.006), (2, 0.008), (3, 0.008)):
            win[idx] += boost * water_extra / 0.35
    elif phase == "rising":
        win[0] += min(0.010, 0.007 * water_extra / 0.35)
        second[1] += 0.004 * water_extra / 0.35

    for idx, lane in enumerate(LANES):
        boat = boats.get(lane, {})
        cls = str(boat.get("class") or "")
        local_win = float(boat.get("local_win") or 0.0)
        runs = boat.get("meeting_runs") or []
        good_finishes = sum(
            1 for run in runs[-4:]
            if str(run.get("finish") or "").startswith(("1", "2", "3"))
        )

        if cls == "A1" and local_win >= 6.5 and good_finishes >= 2:
            win[idx] += 0.010
            second[idx] += 0.006
        elif cls == "A2" and good_finishes >= 2:
            win[idx] += 0.005
            second[idx] += 0.004

        start_time = _number(boat.get("start_time"))
        avg_st = float(boat.get("local_st") or boat.get("avg_st") or 0.18)
        if (
            cls in {"B1", "B2"}
            and start_time is not None
            and start_time >= 0.18
            and avg_st >= 0.18
        ):
            win[idx] -= 0.010
            second[idx] -= 0.003

    for link in links:
        attacker = link["attacker_lane"] - 1
        follower = link["follower_lane"] - 1
        strength = float(link["strength"])

        win[attacker] += 0.006 * strength
        second[attacker] += 0.004 * strength
        second[follower] += 0.012 * strength
        third[follower] += 0.010 * strength

    win = _normalize(win)
    second = _normalize(second)
    third = _normalize(third)

    for idx, row in enumerate(rows):
        row["win"] = win[idx]
        row["second"] = second[idx]
        row["third"] = third[idx]
        row["top3"] = min(1.0, win[idx] + second[idx] + third[idx])

    adjusted["probabilities"] = rows
    adjusted["v21_adjustments"] = {
        "engine": ENGINE_ID,
        "version": ENGINE_VERSION,
        "tide_multiplier": tide_multiplier,
        "tide_phase": phase,
        "attack_links": links,
        "odds_used": False,
        "normalized": True,
    }
    return adjusted
