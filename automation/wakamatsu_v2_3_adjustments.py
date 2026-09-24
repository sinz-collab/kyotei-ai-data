from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import itertools
import math
import re
from typing import Any


ENGINE_ID = "wakamatsu_engine_v2.3"
ENGINE_VERSION = "2.3"
LANES = (1, 2, 3, 4, 5, 6)

# Source: engines/wakamatsu_v2/data/wakamatsu_master_v1.sqlite,
# water_probability_adjustments, condition_key="中潮×昼/序盤", sample_n=667.
# These are the observed finish-position rates used during the 2026-09-23
# candidate validation.  Win rates deliberately stay on the v2.2 path.
FRONT_SECOND_BY_COURSE = {
    1: 0.2068965517241379,
    2: 0.2353823088455772,
    3: 0.1709145427286357,
    4: 0.1544227886056972,
    5: 0.1244377811094453,
    6: 0.1049475262368816,
}
FRONT_THIRD_BY_COURSE = {
    1: 0.1364317841079460,
    2: 0.1709145427286357,
    3: 0.2188905547226387,
    4: 0.1409295352323838,
    5: 0.1529235382308846,
    6: 0.1769115442278860,
}
V22_SECOND_BY_COURSE = {
    1: 0.1761090052248745,
    2: 0.2371683229177338,
    3: 0.2141174060034833,
    4: 0.176109,
    5: 0.115,
    6: 0.081,
}
V22_THIRD_BY_COURSE = {
    1: 0.09281835877471571,
    2: 0.1936277020797049,
    3: 0.1993648191783629,
    4: 0.190,
    5: 0.175,
    6: 0.149,
}


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-", "―"):
        return default
    try:
        parsed = float(re.sub(r"[^0-9.\-]", "", str(value)))
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def normalize_tide_phase(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"rising", "上げ潮", "上げ", "上昇"}:
        return "rising"
    if text in {"falling", "下げ潮", "下げ", "下降"}:
        return "falling"
    return None


def tide_phase_label(value: Any) -> str | None:
    normalized = normalize_tide_phase(value)
    return {"rising": "上げ潮", "falling": "下げ潮"}.get(normalized)


def _minutes(text: Any) -> int | None:
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", str(text or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def _event_kind(event: dict[str, Any]) -> str | None:
    label = str(
        event.get("type")
        or event.get("event")
        or event.get("name")
        or event.get("label")
        or ""
    ).lower()
    if "干潮" in label or "low" in label:
        return "low"
    if "満潮" in label or "high" in label:
        return "high"
    return None


def classify_tide_zone(deadline: Any, tide: dict[str, Any]) -> str:
    """Return the v2.3 mutually-exclusive ±60-minute tide zone."""
    race_minute = _minutes(deadline)
    if race_minute is None:
        return "normal"
    candidates: list[tuple[int, str]] = []
    for event in tide.get("events") or tide.get("tideEvents") or []:
        if not isinstance(event, dict):
            continue
        event_minute = _minutes(event.get("time") or event.get("datetime"))
        kind = _event_kind(event)
        if event_minute is None or kind is None:
            continue
        distance = min(
            abs(race_minute - event_minute),
            abs(race_minute - (event_minute - 24 * 60)),
            abs(race_minute - (event_minute + 24 * 60)),
        )
        candidates.append((distance, kind))
    if not candidates:
        return "normal"
    distance, kind = min(candidates, key=lambda item: item[0])
    if distance > 60:
        return "normal"
    return "low_tide" if kind == "low" else "high_tide"


def tide_zone_label(zone: str) -> str:
    return {
        "low_tide": "干潮±60分",
        "high_tide": "満潮±60分",
        "normal": "通常",
    }[zone]


def _normalize_map(values: dict[str, float]) -> dict[str, float]:
    clipped = {str(lane): max(1e-9, float(values.get(str(lane), 0.0))) for lane in LANES}
    total = sum(clipped.values())
    normalized = {lane: value * 100.0 / total for lane, value in clipped.items()}
    rounded = {lane: round(value, 2) for lane, value in normalized.items()}
    largest = max(rounded, key=rounded.get)
    rounded[largest] = round(rounded[largest] + round(100.0 - sum(rounded.values()), 2), 2)
    return rounded


def _actual_entry_map(race: dict[str, Any]) -> tuple[dict[int, int], str]:
    live = race.get("live") or {}
    direct = live.get("direct") or race.get("direct") or {}
    actual = (
        direct.get("actual_entry")
        or direct.get("actualEntry")
        or live.get("actual_entry")
        or race.get("actual_entry")
    )
    if isinstance(actual, list) and len(actual) == 6:
        lanes = [int(value) for value in actual]
        if sorted(lanes) == list(LANES):
            return {lane: course for course, lane in enumerate(lanes, 1)}, "actual_entry"

    exhibition = live.get("exhibition") or race.get("exhibition") or {}
    mapping: dict[int, int] = {}
    for entry in exhibition.get("entries") or []:
        lane = int(entry.get("lane") or 0)
        course = int(entry.get("exhibition_course") or entry.get("course") or 0)
        if lane in LANES and course in LANES:
            mapping[lane] = course
    if len(mapping) == 6 and sorted(mapping.values()) == list(LANES):
        return mapping, "exhibition_course"

    return {
        int(racer.get("lane")): int(
            racer.get("actual_course") or racer.get("entry_course") or racer.get("lane")
        )
        for racer in race.get("racers") or []
    }, "race_entry"


def _ranked_entries(race: dict[str, Any], kind: str) -> dict[int, dict[str, Any]]:
    live = race.get("live") or {}
    if kind == "exhibition":
        payload = live.get("exhibition") or race.get("exhibition") or {}
    else:
        payload = (
            live.get("original")
            or live.get("original_exhibition")
            or race.get("original")
            or race.get("original_exhibition")
            or {}
        )
    return {
        int(row.get("lane")): row
        for row in payload.get("entries") or []
        if int(row.get("lane") or 0) in LANES
    }


def _original_ranks(entries: dict[int, dict[str, Any]]) -> dict[int, int]:
    scored = []
    for lane, row in entries.items():
        value = _number(row.get("sum"), None)
        if value is None:
            lap = _number(row.get("lap_time"), None)
            show = _number(row.get("sum_exhibition"), None)
            value = None if lap is None or show is None else lap + show
        if value is not None:
            scored.append((value, lane))
    scored.sort()
    return {lane: rank for rank, (_, lane) in enumerate(scored, 1)}


def _front_water(prediction: dict[str, Any]) -> bool:
    context = prediction.get("raceContext") or {}
    return (
        context.get("tide_type") == "中潮"
        and context.get("time_band") in {"昼/序盤", "ナイター前半"}
        and context.get("water_type") in {"2差し浮上型", "4浮上型"}
    )


def _replace_front_place_bases(
    prediction: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> bool:
    if not _front_water(prediction):
        return False
    for field, observed, previous in (
        ("second", FRONT_SECOND_BY_COURSE, V22_SECOND_BY_COURSE),
        ("third", FRONT_THIRD_BY_COURSE, V22_THIRD_BY_COURSE),
    ):
        current = prediction.get(field) or {}
        replaced = {}
        for lane in LANES:
            course = entry_by_lane.get(lane, lane)
            ratio = observed[course] / previous[course]
            replaced[str(lane)] = float(current.get(str(lane), 0.0)) * ratio
        prediction[field] = _normalize_map(replaced)
    prediction.setdefault("diagnostics", {})["frontWaterPlaceBase"] = {
        "applied": True,
        "source": "wakamatsu_master_v1.sqlite/water_probability_adjustments",
        "conditionKey": "中潮×昼/序盤",
        "sampleN": 667,
        "winBasePreserved": True,
        "secondByCourse": {str(k): round(v * 100.0, 6) for k, v in FRONT_SECOND_BY_COURSE.items()},
        "thirdByCourse": {str(k): round(v * 100.0, 6) for k, v in FRONT_THIRD_BY_COURSE.items()},
        "downstreamTidePlaceAdjustmentApplied": False,
    }
    return True


def _escape_rate(race: dict[str, Any]) -> float | None:
    racer = next((row for row in race.get("racers") or [] if int(row.get("lane") or 0) == 1), {})
    return _number(racer.get("boaters_escape_rate"), None)


def _ability_supported(race: dict[str, Any], lane: int, final_win: float) -> bool:
    racer = next((row for row in race.get("racers") or [] if int(row.get("lane") or 0) == lane), {})
    return (
        final_win >= 15.0
        or str(racer.get("class") or "") in {"A1", "A2"}
        or float(_number(racer.get("nat_win"), 0.0) or 0.0) >= 5.5
    )


def _water_supports_course(prediction: dict[str, Any], course: int) -> bool:
    water_type = str((prediction.get("raceContext") or {}).get("water_type") or "")
    return (
        (course == 2 and water_type == "2差し浮上型")
        or (course == 3 and water_type == "3攻め型")
        or (course == 4 and water_type == "4浮上型")
    )


def _strong_attack(
    race: dict[str, Any],
    pre: dict[str, Any],
    final: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> dict[str, Any]:
    exhibition = _ranked_entries(race, "exhibition")
    original_ranks = _original_ranks(_ranked_entries(race, "original"))
    candidates = []
    for lane in LANES:
        course = entry_by_lane.get(lane, lane)
        if course not in (2, 3, 4):
            continue
        final_win = float((final.get("win") or {}).get(str(lane), 0.0))
        delta = final_win - float((pre.get("win") or {}).get(str(lane), 0.0))
        row = exhibition.get(lane, {})
        checks = {
            "waterSupportsCourse": _water_supports_course(final, course),
            "exhibitionTop2": int(row.get("exhibition_rank") or 99) <= 2,
            "originalTop2": original_ranks.get(lane, 99) <= 2,
            "finalWinDeltaAtLeast3pt": delta >= 3.0,
            "courseOrAbility": _ability_supported(race, lane, final_win),
        }
        count = sum(checks.values())
        candidates.append({
            "lane": lane,
            "course": course,
            "matched": count,
            "checks": checks,
            "finalWin": round(final_win, 2),
            "deltaWin": round(delta, 2),
            "active": count >= 4,
        })
    active = [row for row in candidates if row["active"]]
    if active:
        win = {str(lane): float((final.get("win") or {}).get(str(lane), 0.0)) for lane in LANES}
        for row in active:
            # Bounded composite correction.  It never assigns or forces TOP1.
            lane_key = str(row["lane"])
            win[lane_key] *= 1.0 + min(0.08, 0.04 + 0.01 * (row["matched"] - 4))
        final["win"] = _normalize_map(win)
    return {
        "active": bool(active),
        "candidates": candidates,
        "attackLanes": [row["lane"] for row in active],
        "rule": "at least four independent conditions; exhibition ST alone is never a condition",
        "top1Forced": False,
    }


def _slit_attack(
    race: dict[str, Any],
    final: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> dict[str, Any]:
    exhibition = _ranked_entries(race, "exhibition")
    original_ranks = _original_ranks(_ranked_entries(race, "original"))
    lane1_st_rank = int((exhibition.get(1) or {}).get("start_rank") or 99)
    escape_rate = _escape_rate(race)
    candidates = []
    for lane in LANES:
        course = entry_by_lane.get(lane, lane)
        if course not in (3, 4):
            continue
        row = exhibition.get(lane, {})
        final_win = float((final.get("win") or {}).get(str(lane), 0.0))
        independent_offset = (
            int(row.get("start_rank") or 99) <= 2
            and int(row.get("exhibition_rank") or 99) <= 2
            and _ability_supported(race, lane, final_win)
        )
        original_ok = original_ranks.get(lane, 99) <= 5 or independent_offset
        checks = {
            "lane1StartRank5to6": lane1_st_rank in (5, 6),
            "attackerStartTop2": int(row.get("start_rank") or 99) <= 2,
            "attackerExhibitionTop2": int(row.get("exhibition_rank") or 99) <= 2,
            "finalWinAtLeast10": final_win >= 10.0,
            "lane1EscapeBelow75": escape_rate is not None and escape_rate < 75.0,
            "originalNotExtremeOrOffset": original_ok,
        }
        candidates.append({
            "lane": lane,
            "course": course,
            "checks": checks,
            "active": all(checks.values()),
            "finalWin": round(final_win, 2),
            "originalRank": original_ranks.get(lane),
        })
    active = [row for row in candidates if row["active"]]
    return {
        "active": bool(active),
        "attackLane": active[0]["lane"] if active else None,
        "attackCourse": active[0]["course"] if active else None,
        "lane1StartRank": lane1_st_rank if lane1_st_rank != 99 else None,
        "lane1EscapeRate": escape_rate,
        "candidates": candidates,
        "rule": "all six independent conditions required; exhibition ST alone is prohibited",
    }


def _combo_probability(combo: tuple[int, int, int], prediction: dict[str, Any]) -> float:
    a, b, c = combo
    win = {lane: float((prediction.get("win") or {}).get(str(lane), 0.0)) / 100.0 for lane in LANES}
    second = {lane: float((prediction.get("second") or {}).get(str(lane), 0.0)) / 100.0 for lane in LANES}
    third = {lane: float((prediction.get("third") or {}).get(str(lane), 0.0)) / 100.0 for lane in LANES}
    p2 = second[b] / max(1e-12, sum(value for lane, value in second.items() if lane != a))
    p3 = third[c] / max(1e-12, sum(value for lane, value in third.items() if lane not in (a, b)))
    return win[a] * p2 * p3


def _scenario_multiplier(
    combo: tuple[int, int, int],
    prediction: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> tuple[float, list[str]]:
    a, b, c = combo
    scenarios = prediction.get("scenarios") or {}
    course = entry_by_lane.get(a, a)
    multiplier = 1.0
    tags: list[str] = []
    if a == 1:
        multiplier *= 1.0 + float(scenarios.get("nige", 0.0)) * 0.16
        tags.append("nige")
    if course == 2:
        multiplier *= 1.0 + float(scenarios.get("sashi_2", 0.0)) * 0.22
        tags.append("course2_attack")
    elif course == 3:
        multiplier *= 1.0 + (
            float(scenarios.get("makuri_3", 0.0))
            + float(scenarios.get("makurizashi_3", 0.0))
        ) * 0.19
        tags.append("course3_attack")
    elif course == 4:
        multiplier *= 1.0 + (
            float(scenarios.get("makuri_4", 0.0))
            + float(scenarios.get("makurizashi_4", 0.0))
        ) * 0.18
        tags.append("course4_attack")
    outside = next((lane for lane, value in entry_by_lane.items() if value == course + 1), None)
    if outside in (b, c):
        multiplier *= 1.12
        tags.append("direct_outside_link")
    if a != 1 and 1 in (b, c):
        multiplier *= 1.08
        tags.append("inside_remain")
    if a in (5, 6):
        multiplier *= 1.0 + float(scenarios.get("outer_attack", 0.0)) * 0.14
        tags.append("outer_attack")
    if float(scenarios.get("chaos", 0.0)) >= 0.13 and a != 1:
        multiplier *= 1.05
        tags.append("chaos")
    return multiplier, tags


def _rank_tickets(
    prediction: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> list[dict[str, Any]]:
    rows = []
    for combo in itertools.permutations(LANES, 3):
        multiplier, tags = _scenario_multiplier(combo, prediction, entry_by_lane)
        raw = _combo_probability(combo, prediction)
        rows.append({
            "combo": "-".join(map(str, combo)),
            "probability": raw * multiplier,
            "rawProbability": raw,
            "scenarioMultiplier": multiplier,
            "scenarioTags": tags,
            "head": combo[0],
        })
    total = sum(row["probability"] for row in rows)
    for row in rows:
        row["probability"] /= max(total, 1e-12)
    rows.sort(key=lambda row: row["probability"], reverse=True)
    return rows


def _player_course_fallback_count(prediction: dict[str, Any]) -> int:
    reflection = (prediction.get("diagnostics") or {}).get("fullReflection") or {}
    base_audit = reflection.get("baseAudit") or {}
    count = 0
    for lane in LANES:
        lane_audit = base_audit.get(str(lane)) or base_audit.get(lane) or {}
        player_course_rows = [
            (lane_audit.get(finish) or {}).get("player_course") or {}
            for finish in ("win", "second", "third")
        ]
        if any(
            row.get("available") is False and row.get("fallback") == "course_prior"
            for row in player_course_rows
        ):
            count += 1
    return count


def _classify_full_reflection(prediction: dict[str, Any]) -> None:
    reflection = (prediction.get("diagnostics") or {}).get("fullReflection")
    if not isinstance(reflection, dict):
        return
    base_audit = reflection.get("baseAudit") or {}
    fallback_count = _player_course_fallback_count(prediction)
    available_count = 0
    for lane in LANES:
        lane_audit = base_audit.get(str(lane)) or base_audit.get(lane) or {}
        player_course = (lane_audit.get("win") or {}).get("player_course") or {}
        available_count += player_course.get("available") is True

    input_audit = reflection.get("inputAudit") or {}
    missing_critical = set(input_audit.get("missingCritical") or [])
    fallback_only = missing_critical.issubset({"playerCourseDb"})
    if fallback_count and available_count and fallback_only:
        status = "complete_with_fallback"
    elif fallback_count == 0 and available_count == len(LANES) and not missing_critical:
        status = "complete"
    else:
        status = "partial"
    reflection["status"] = status
    reflection["playerCourseStatus"] = {
        "status": status,
        "availableCount": available_count,
        "fallbackCount": fallback_count,
        "fallback": "course_prior" if fallback_count else None,
    }


def _replace_tail_ticket(
    selected: list[dict[str, Any]],
    candidate: dict[str, Any],
    tag: str,
    protected: set[str],
) -> str | None:
    if candidate["combo"] in {row["combo"] for row in selected}:
        return None
    for index in (9, 8):
        if index >= len(selected) or selected[index]["combo"] in protected:
            continue
        replaced = selected[index]["combo"]
        row = deepcopy(candidate)
        row["scenarioTags"] = list(row["scenarioTags"]) + [tag]
        selected[index] = row
        return replaced
    return None


def _three_attack_link_candidate(
    prediction: dict[str, Any],
    strong: dict[str, Any],
    slit: dict[str, Any],
    by_combo: dict[str, dict[str, Any]],
    existing: set[str],
) -> dict[str, Any] | None:
    win = prediction.get("win") or {}
    third = prediction.get("third") or {}
    win_top3 = sorted(LANES, key=lambda lane: (-float(win.get(str(lane), 0.0)), lane))[:3]
    conditions_met = (
        (prediction.get("raceContext") or {}).get("water_type") == "3攻め型"
        and 3 in win_top3
        and float(win.get("3", 0.0)) >= 10.0
        and float(third.get("4", 0.0)) >= 10.0
        and not strong.get("active")
        and not slit.get("active")
    )
    if not conditions_met:
        return None
    candidates = [
        by_combo[combo]
        for combo in ("1-3-4", "3-1-4", "3-4-1")
        if combo in by_combo and combo not in existing
    ]
    return max(candidates, key=lambda row: row["probability"], default=None)


def _development_protection_candidate(
    prediction: dict[str, Any],
    entry_by_lane: dict[int, int],
    by_combo: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, int | None, int | None, float | None, str]:
    win = prediction.get("win") or {}
    second = prediction.get("second") or {}
    third = prediction.get("third") or {}
    scenarios = prediction.get("scenarios") or {}
    win_top3 = sorted(LANES, key=lambda lane: (-float(win.get(str(lane), 0.0)), lane))[:3]

    attack_candidates = []
    for lane in win_top3:
        course = entry_by_lane.get(lane, lane)
        if lane == 1 or float(win.get(str(lane), 0.0)) < 10.0 or course not in (2, 3, 4):
            continue
        if course == 2:
            scenario_score = float(scenarios.get("sashi_2", 0.0))
        elif course == 3:
            scenario_score = float(scenarios.get("makuri_3", 0.0)) + float(
                scenarios.get("makurizashi_3", 0.0)
            )
        else:
            scenario_score = float(scenarios.get("makuri_4", 0.0)) + float(
                scenarios.get("makurizashi_4", 0.0)
            )
        if scenario_score >= 0.15:
            attack_candidates.append((scenario_score, float(win.get(str(lane), 0.0)), lane, course))
    if not attack_candidates:
        return None, None, None, None, "no_attack_candidate"
    scenario_score, _, attack_lane, attack_course = max(
        attack_candidates, key=lambda row: (row[0], row[1], -row[2])
    )

    second_top3 = sorted(
        LANES, key=lambda lane: (-float(second.get(str(lane), 0.0)), lane)
    )[:3]
    if 1 not in second_top3:
        return None, attack_lane, attack_course, scenario_score, "lane1_not_second_top3"

    third_top3 = sorted(
        LANES, key=lambda lane: (-float(third.get(str(lane), 0.0)), lane)
    )[:3]
    third_candidates = [
        lane
        for lane in third_top3
        if lane not in (attack_lane, 1) and float(third.get(str(lane), 0.0)) >= 10.0
    ]
    if not third_candidates:
        return None, attack_lane, attack_course, scenario_score, "no_third_candidate"
    third_lane = max(third_candidates, key=lambda lane: (float(third.get(str(lane), 0.0)), -lane))
    combo = f"{attack_lane}-1-{third_lane}"
    return by_combo.get(combo), attack_lane, attack_course, scenario_score, "candidate_ready"


def _reserved_attack_tickets(
    slit: dict[str, Any],
    entry_by_lane: dict[int, int],
) -> list[str]:
    if not slit.get("active"):
        return []
    attacker = int(slit["attackLane"])
    course = int(slit["attackCourse"])
    by_course = {course_value: lane for lane, course_value in entry_by_lane.items()}
    if course == 3:
        linked = by_course.get(4)
        return [
            f"{attacker}-{linked}-1",
            f"{attacker}-1-{linked}",
            f"{attacker}-{linked}-{by_course.get(5)}",
        ]
    lane5, lane6 = by_course.get(5), by_course.get(6)
    return [
        f"{attacker}-{lane5}-1",
        f"{attacker}-{lane5}-{by_course.get(2)}",
        f"{attacker}-{lane6}-1",
    ]


def _set_tickets(
    prediction: dict[str, Any],
    entry_by_lane: dict[int, int],
    strong: dict[str, Any],
    slit: dict[str, Any],
) -> None:
    previous_combos = [
        row.get("combo")
        for row in prediction.get("tickets") or []
        if isinstance(row, dict) and row.get("combo")
    ]
    ranked = _rank_tickets(prediction, entry_by_lane)
    by_combo = {row["combo"]: row for row in ranked}
    rank_by_combo = {row["combo"]: rank for rank, row in enumerate(ranked, 1)}
    reserved = [combo for combo in _reserved_attack_tickets(slit, entry_by_lane) if combo in by_combo]
    normal_count = 10 - min(3, len(reserved))
    selected = []
    for row in ranked:
        if row["combo"] in reserved:
            continue
        selected.append(row)
        if len(selected) == normal_count:
            break
    for combo in reserved[: 10 - len(selected)]:
        row = deepcopy(by_combo[combo])
        row["scenarioTags"] = list(row["scenarioTags"]) + ["slit_attack_reserved"]
        selected.append(row)

    # Normal races retain at most one v2.2 top-10 combination when the new
    # observed place base moves it only to the adjacent 11th rank.  This is a
    # bounded continuity rule, independent of race results, that implements
    # the requirement not to discard ordinary core tickets unnecessarily.
    continuity = []
    if not slit.get("active"):
        continuity = [
            combo for combo in previous_combos
            if combo not in {row["combo"] for row in selected}
            and rank_by_combo.get(combo) == 11
        ][:1]
        for combo in continuity:
            selected[-1] = deepcopy(by_combo[combo])
            selected[-1]["scenarioTags"] = list(selected[-1]["scenarioTags"]) + [
                "v22_core_continuity_rank11"
            ]

    original_selected = [deepcopy(row) for row in selected]
    protected: set[str] = set(reserved)
    protection_steps = []

    three_attack = _three_attack_link_candidate(
        prediction,
        strong,
        slit,
        by_combo,
        {row["combo"] for row in selected},
    )
    if three_attack is not None:
        replaced = _replace_tail_ticket(
            selected,
            three_attack,
            "three_attack_lane4_link_protected",
            protected,
        )
        if replaced is not None:
            protected.add(three_attack["combo"])
            protection_steps.append({
                "reason": "three_attack_lane4_link",
                "addedCombo": three_attack["combo"],
                "replacedCombo": replaced,
                "attackLane": 3,
                "attackCourse": entry_by_lane.get(3, 3),
                "attackScenarioScore": None,
            })

    fallback_count = _player_course_fallback_count(prediction)
    original_sab = prediction.get("sab")
    effective_grade = "A" if original_sab == "S" and fallback_count >= 2 else original_sab
    original_heads = {row["head"] for row in original_selected}
    development_reason = "top10_heads_not_concentrated"
    attack_lane = attack_course = None
    attack_score = None
    if len(original_heads) == 1:
        if effective_grade == "S":
            development_reason = "s_grade_concentration_kept"
        else:
            candidate, attack_lane, attack_course, attack_score, development_reason = (
                _development_protection_candidate(prediction, entry_by_lane, by_combo)
            )
            if candidate is not None:
                if candidate["combo"] in {row["combo"] for row in selected}:
                    development_reason = "candidate_already_present"
                else:
                    replaced = _replace_tail_ticket(
                        selected,
                        candidate,
                        "same_head_development_protected",
                        protected,
                    )
                    if replaced is not None:
                        protected.add(candidate["combo"])
                        development_reason = "same_head_development"
                        protection_steps.append({
                            "reason": development_reason,
                            "addedCombo": candidate["combo"],
                            "replacedCombo": replaced,
                            "attackLane": attack_lane,
                            "attackCourse": attack_course,
                            "attackScenarioScore": round(float(attack_score), 6),
                        })

    last_step = protection_steps[-1] if protection_steps else {}
    reason = "+".join(step["reason"] for step in protection_steps) or development_reason
    prediction.setdefault("diagnostics", {})["ticketProtection"] = {
        "applied": bool(protection_steps),
        "reason": reason,
        "addedCombo": last_step.get("addedCombo"),
        "replacedCombo": last_step.get("replacedCombo"),
        "attackLane": last_step.get("attackLane", attack_lane),
        "attackCourse": last_step.get("attackCourse", attack_course),
        "attackScenarioScore": last_step.get("attackScenarioScore", attack_score),
        "playerCourseFallbackCount": fallback_count,
        "originalSab": original_sab,
        "effectiveTicketGrade": effective_grade,
        "oddsUsed": False,
        "steps": protection_steps,
    }

    role_names = ["本線"] * 6 + ["ズレ対応"] * 2 + ["荒れ対応"] * 2
    tickets = []
    for rank, (row, role) in enumerate(zip(selected, role_names), 1):
        tickets.append({
            "combo": row["combo"],
            "role": role,
            "probability": round(row["probability"] * 100.0, 3),
            "scenarioTags": row["scenarioTags"],
            "odds": "-",
            "rank": rank,
        })
    prediction["tickets"] = tickets
    prediction["ai"] = tickets[:8]
    prediction["aiUpset"] = tickets[8:]
    prediction.setdefault("diagnostics", {})["ticketRankAuditV23"] = [
        {
            "rank": index,
            "combo": row["combo"],
            "probability": round(row["probability"] * 100.0, 5),
            "rawProbability": round(row["rawProbability"] * 100.0, 5),
            "scenarioMultiplier": round(row["scenarioMultiplier"], 6),
        }
        for index, row in enumerate(ranked[:20], 1)
    ]
    prediction["diagnostics"]["slitAttackReservedTickets"] = reserved[:3]
    prediction["diagnostics"]["continuityTickets"] = continuity


def _set_sab(prediction: dict[str, Any], strong: dict[str, Any], slit: dict[str, Any]) -> None:
    win = sorted((float(value) for value in (prediction.get("win") or {}).values()), reverse=True)
    second = [float(value) for value in (prediction.get("second") or {}).values()]
    third = [float(value) for value in (prediction.get("third") or {}).values()]
    head_candidates = sum(value >= 10.0 for value in win)
    second_branches = sum(value >= 15.0 for value in second)
    third_branches = sum(value >= 15.0 for value in third)
    score = (
        0.45 * min(1.0, win[0] / 52.0)
        + 0.25 * min(1.0, (win[0] - win[1]) / 22.0)
        + 0.15 * max(0.0, 1.0 - (head_candidates - 1) / 4.0)
        + 0.075 * max(0.0, 1.0 - (second_branches - 1) / 4.0)
        + 0.075 * max(0.0, 1.0 - (third_branches - 1) / 4.0)
    )
    override = bool(strong.get("active") or slit.get("active"))
    if override:
        score -= 0.10
    grade = "S" if score >= 0.70 else "A" if score >= 0.52 else "B"
    if override and grade == "S":
        grade = "A"
    prediction["sab"] = grade
    prediction["sabDetail"] = {
        "grade": grade,
        "score": round(max(0.0, min(1.0, score)), 4),
        "head_concentration": round(win[0] / 100.0, 4),
        "head_gap": round((win[0] - win[1]) / 100.0, 4),
        "headCandidateCount": head_candidates,
        "secondBranchCount": second_branches,
        "thirdBranchCount": third_branches,
        "attackOverride": override,
        "gradeCap": "A" if override else None,
        "meaning": "confidence from head concentration and finish-position branching",
    }
    prediction["confidence"] = round(prediction["sabDetail"]["score"] * 100.0, 1)


def _refresh_readability(prediction: dict[str, Any]) -> None:
    ranked = sorted((prediction.get("win") or {}), key=(prediction.get("win") or {}).get, reverse=True)
    prediction["readability"] = {
        "axisLane": int(ranked[0]),
        "secondHeadLane": int(ranked[1]),
        "axisGap": round(
            float(prediction["win"][ranked[0]]) - float(prediction["win"][ranked[1]]),
            2,
        ),
    }


def _refresh_probability_review(pre: dict[str, Any], final: dict[str, Any]) -> None:
    review: dict[str, dict[str, float]] = {}
    for lane in LANES:
        key = str(lane)
        pre_win = float((pre.get("win") or {}).get(key, 0.0))
        pre_second = float((pre.get("second") or {}).get(key, 0.0))
        pre_third = float((pre.get("third") or {}).get(key, 0.0))
        final_win = float((final.get("win") or {}).get(key, 0.0))
        final_second = float((final.get("second") or {}).get(key, 0.0))
        final_third = float((final.get("third") or {}).get(key, 0.0))
        review[key] = {
            "morningWin": round(pre_win, 1),
            "morningSecond": round(pre_second, 1),
            "morningThird": round(pre_third, 1),
            "deltaWin": round(final_win - pre_win, 1),
            "deltaSecond": round(final_second - pre_second, 1),
            "deltaThird": round(final_third - pre_third, 1),
        }
    final["probabilityReviewStatus"] = "reviewed"
    final["probabilityReview"] = review


def apply_v23_to_payload(payload: dict[str, Any]) -> dict[str, Any]:
    adjusted = deepcopy(payload)
    tide = adjusted.get("tide") or {}
    for race in adjusted.get("races") or []:
        pre = race.get("predictionPre")
        final = race.get("predictionFinal")
        active = final if isinstance(final, dict) else race.get("prediction")
        if not isinstance(active, dict):
            continue

        entry_by_lane, entry_source = _actual_entry_map(race)
        for prediction in (pre, final):
            if not isinstance(prediction, dict):
                continue
            context = prediction.setdefault("raceContext", {})
            phase = normalize_tide_phase(context.get("tide_phase"))
            zone = classify_tide_zone(race.get("deadline"), tide)
            context["tide_phase"] = phase
            context["tide_phase_label"] = tide_phase_label(phase)
            context["tide_zone"] = zone
            context["tide_zone_label"] = tide_zone_label(zone)
            context["actualEntrySource"] = entry_source
            context["actualEntryByLane"] = {str(k): v for k, v in entry_by_lane.items()}
            _replace_front_place_bases(prediction, entry_by_lane)

        if isinstance(final, dict) and isinstance(pre, dict):
            strong = _strong_attack(race, pre, final, entry_by_lane)
            slit = _slit_attack(race, final, entry_by_lane)
            final.setdefault("diagnostics", {})["strongAttackOverride"] = strong
            final["diagnostics"]["slitAttackOverride"] = slit
            final["diagnostics"]["oddsUsedForPrediction"] = False
            final["diagnostics"]["actualEntryAppliedBeforeBase"] = True
            _classify_full_reflection(final)
            _set_sab(final, strong, slit)
            _set_tickets(final, entry_by_lane, strong, slit)
            _refresh_readability(final)
            _refresh_probability_review(pre, final)
            final["predictionStage"] = {
                "label": "本予想",
                "badge": "本予想",
                "statusText": "直前・展示・オリ展示を反映して若松v2.3で再精査済み",
                "color": "green",
            }
            race["prediction"] = final
            active = final
        elif isinstance(active, dict):
            empty_override = {"active": False, "attackLanes": []}
            active.setdefault("diagnostics", {})["strongAttackOverride"] = empty_override
            active["diagnostics"]["slitAttackOverride"] = {"active": False, "attackLane": None}
            active["diagnostics"]["oddsUsedForPrediction"] = False
            active["diagnostics"]["actualEntryAppliedBeforeBase"] = True
            _classify_full_reflection(active)
            _set_sab(active, empty_override, {"active": False})
            _set_tickets(active, entry_by_lane, empty_override, {"active": False})
            _refresh_readability(active)

        for prediction in (pre, final, active):
            if not isinstance(prediction, dict):
                continue
            prediction["engine"] = ENGINE_ID
            prediction["engineVersion"] = ENGINE_VERSION
            prediction.setdefault("diagnostics", {})["oddsUsedForPrediction"] = False
            prediction["diagnostics"]["v23"] = {
                "normalized": True,
                "oddsUsed": False,
                "tideCorrectionDeduplicated": True,
                "actualEntryBeforeBase": True,
            }

    adjusted["engine"] = ENGINE_ID
    adjusted["engineVersion"] = ENGINE_VERSION
    adjusted["preds"] = {
        str(int(race.get("race") or 0)): race.get("prediction")
        for race in adjusted.get("races") or []
    }
    adjusted["predictionEngine"] = {
        **(adjusted.get("predictionEngine") or {}),
        "id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "generatedBy": "automation/apply_wakamatsu_v2_3.py",
        "oddsUsedForProbability": False,
    }
    return adjusted
