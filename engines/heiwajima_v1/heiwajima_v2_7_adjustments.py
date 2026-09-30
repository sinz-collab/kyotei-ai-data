from __future__ import annotations

import math
import re
from itertools import permutations
from typing import Any


HISTORY_RACE_COUNT = 5972
HISTORY_WEIGHT = 0.50
CURRENT_WEIGHT = 0.30
SCENARIO_WEIGHT = 0.20
HISTORY_SHRINK_SAMPLE = 120.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _rate(value: Any) -> float:
    parsed = _num(value, 0.0)
    return parsed / 100.0 if abs(parsed) > 1.0 else parsed


def _normalise(values: dict[Any, float]) -> dict[Any, float]:
    total = sum(max(0.0, float(value)) for value in values.values())
    if total <= 0.0:
        equal = 1.0 / max(1, len(values))
        return {key: equal for key in values}
    return {key: max(0.0, float(value)) / total for key, value in values.items()}


def _finish(value: Any) -> int | None:
    match = re.search(r"[1-6]", str(value or ""))
    return int(match.group()) if match else None


def _kimarite(boat: dict) -> dict:
    inline = boat.get("kimarite") or {}
    if not isinstance(inline, dict):
        inline = {}
    raw_values = (
        inline.get("escape_rate", boat.get("boaters_escape_rate")),
        inline.get("sashare_rate", boat.get("boaters_sashare_rate")),
        inline.get("makurare_rate", boat.get("boaters_makurare_rate")),
        inline.get("makurare_zashi_rate", boat.get("boaters_makurare_zashi_rate")),
    )
    return {
        "available": any(value not in (None, "", "-") for value in raw_values),
        "escape": _rate(inline.get("escape_rate", boat.get("boaters_escape_rate"))),
        "sashare": _rate(inline.get("sashare_rate", boat.get("boaters_sashare_rate"))),
        "makurare": _rate(inline.get("makurare_rate", boat.get("boaters_makurare_rate"))),
        "makurare_zashi": _rate(
            inline.get("makurare_zashi_rate", boat.get("boaters_makurare_zashi_rate"))
        ),
    }


def lane1_weakness(boats: list[dict]) -> dict:
    lane1 = next((boat for boat in boats if int(boat.get("boat_no") or boat.get("lane") or 0) == 1), {})
    rates = _kimarite(lane1)
    vulnerability = rates["sashare"] + rates["makurare"] + rates["makurare_zashi"]
    activated = bool(rates["available"]) and (
        rates["escape"] < 0.40 or vulnerability >= 0.35
    )
    return {
        "activated": activated,
        "escapeRate": round(rates["escape"], 6),
        "vulnerabilityRate": round(vulnerability, 6),
    }


def evaluate_s16(boats: list[dict], tide: dict | None) -> dict:
    by_lane = {
        int(boat.get("boat_no") or boat.get("lane") or 0): boat
        for boat in boats
    }
    lane6 = by_lane.get(6, {})
    lane1 = by_lane.get(1, {})

    klass = str(lane6.get("class") or "").upper()
    max_win = max(
        _num(lane6.get("nat_win_score", lane6.get("nat_win"))),
        _num(lane6.get("local_win_score", lane6.get("local_win"))),
    )
    signal_a = klass in ("A1", "A2") or max_win >= 5.8

    recent = lane6.get("motor_recent") or {}
    signal_b = (
        str(recent.get("trend") or "").lower() == "up"
        or _num(recent.get("top3_rate")) >= 55.0
        or _num(recent.get("top2_rate")) >= 40.0
    )
    signal_c = any(_finish(run.get("finish")) in (1, 2) for run in lane6.get("season_runs") or [])

    one = _kimarite(lane1)
    vulnerability = one["sashare"] + one["makurare"] + one["makurare_zashi"]
    signal_d = bool(one["available"]) and (
        one["escape"] < 0.40 or vulnerability >= 0.35
    )

    tide = tide or {}
    band = str(tide.get("tide_level_band") or tide.get("level_band") or "").lower()
    low_band = bool(tide.get("low_water_band")) or "low" in band or "低" in band
    minutes_to_low = _num(
        tide.get("minutes_to_low_tide", tide.get("minutes_to_next_low")),
        9999.0,
    )
    signal_e = low_band and 0.0 <= minutes_to_low <= 120.0

    signals = {
        "outerPlayerStrength": signal_a,
        "motorRise": signal_b,
        "meetingTop2": signal_c,
        "lane1Vulnerability": signal_d,
        "lowTideFit": signal_e,
    }
    count = sum(bool(value) for value in signals.values())
    level = "strong" if count == 5 else "medium" if count == 4 else "off"
    return {
        "activated": level != "off",
        "level": level,
        "signalCount": count,
        "signals": signals,
    }


def apply_p1_adjustments(
    records: list[dict], boats: list[dict], scenarios: list[dict], s16: dict
) -> dict:
    probs = {int(row["boat_no"]): float(row["win_prob"]) for row in records}
    weakness = lane1_weakness(boats)
    transfer = 0.0

    if weakness["activated"]:
        scenario_prob = {str(row.get("id")): float(row.get("probability") or 0.0) for row in scenarios}
        attack_weights = {
            2: scenario_prob.get("S03_2_SASHI", 0.0) + 0.25 * scenario_prob.get("S11_WALL_FAILURE", 0.0),
            3: scenario_prob.get("S05_3_MAKURI", 0.0)
            + scenario_prob.get("S06_3_MAKURIZASHI", 0.0)
            + scenario_prob.get("S07_3_ATTACK_LINK", 0.0)
            + 0.25 * scenario_prob.get("S11_WALL_FAILURE", 0.0)
            + 0.25 * scenario_prob.get("S12_INSIDE_COLLAPSE", 0.0),
            4: scenario_prob.get("S08_4_KADO", 0.0)
            + scenario_prob.get("S09_4_ATTACK_LINK", 0.0)
            + 0.50 * scenario_prob.get("S10_1_3_BATTLE", 0.0)
            + 0.25 * scenario_prob.get("S11_WALL_FAILURE", 0.0)
            + 0.25 * scenario_prob.get("S12_INSIDE_COLLAPSE", 0.0),
            5: scenario_prob.get("S15_4_ATTACK_5_HEAD", 0.0)
            + 0.50 * scenario_prob.get("S10_1_3_BATTLE", 0.0)
            + 0.25 * scenario_prob.get("S11_WALL_FAILURE", 0.0)
            + 0.25 * scenario_prob.get("S12_INSIDE_COLLAPSE", 0.0),
            6: 0.50 * scenario_prob.get("S12_INSIDE_COLLAPSE", 0.0)
            + 0.15 * scenario_prob.get("S07_3_ATTACK_LINK", 0.0)
            + 0.15 * scenario_prob.get("S09_4_ATTACK_LINK", 0.0)
            + 0.10 * scenario_prob.get("S11_WALL_FAILURE", 0.0),
        }
        attack_weights = _normalise(attack_weights)
        weakness_strength = max(0.0, 0.40 - weakness["escapeRate"]) + max(
            0.0, weakness["vulnerabilityRate"] - 0.35
        )
        transfer = probs[1] * min(0.18, 0.08 + weakness_strength * 0.25)
        probs[1] -= transfer
        for lane, weight in attack_weights.items():
            probs[lane] += transfer * weight

    s16_delta = 0.04 if s16.get("level") == "strong" else 0.02 if s16.get("level") == "medium" else 0.0
    if s16_delta:
        probs[6] += s16_delta

    probs = _normalise(probs)
    for row in records:
        row["win_prob"] = round(probs[int(row["boat_no"])], 6)

    return {
        "lane1Weakness": weakness,
        "redistributedProbability": round(transfer, 6),
        "s16ProbabilityDelta": s16_delta,
    }


def _history_pairs(history_rows) -> dict[int, dict[tuple[int, int], float]]:
    rows = history_rows.to_dict("records") if hasattr(history_rows, "to_dict") else list(history_rows or [])
    valid = [
        row for row in rows
        if int(_num(row.get("first_lane"))) in range(1, 7)
        and int(_num(row.get("second_lane"))) in range(1, 7)
        and int(_num(row.get("third_lane"))) in range(1, 7)
    ]
    if len(valid) != HISTORY_RACE_COUNT:
        raise RuntimeError(f"heiwajima_history_count_mismatch: expected={HISTORY_RACE_COUNT} actual={len(valid)}")

    output: dict[int, dict[tuple[int, int], float]] = {}
    for head in range(1, 7):
        keys = [(second, third) for second, third in permutations(range(1, 7), 2) if head not in (second, third)]
        counts = {key: 0.0 for key in keys}
        for row in valid:
            if int(row["first_lane"]) == head:
                key = (int(row["second_lane"]), int(row["third_lane"]))
                if key in counts:
                    counts[key] += 1.0
        sample = sum(counts.values())
        prior = 1.0 / len(keys)
        output[head] = {
            key: (count + HISTORY_SHRINK_SAMPLE * prior) / (sample + HISTORY_SHRINK_SAMPLE)
            for key, count in counts.items()
        }
    return output


def _current_pairs(records: list[dict], head: int) -> dict[tuple[int, int], float]:
    by_lane = {int(row["boat_no"]): row for row in records}
    values = {
        (second, third): float(by_lane[second]["second_prob"]) * float(by_lane[third]["third_prob"])
        for second, third in permutations(range(1, 7), 2)
        if head not in (second, third)
    }
    return _normalise(values)


def _scenario_pairs(scenarios: list[dict], head: int) -> dict[tuple[int, int], float]:
    keys = [(second, third) for second, third in permutations(range(1, 7), 2) if head not in (second, third)]
    values = {key: 0.0 for key in keys}
    for scenario in scenarios:
        if head not in [int(value) for value in scenario.get("head") or []]:
            continue
        probability = float(scenario.get("probability") or 0.0)
        seconds = [int(value) for value in scenario.get("second") or [] if int(value) != head]
        thirds = [int(value) for value in scenario.get("third") or [] if int(value) != head]
        for second_index, second in enumerate(seconds):
            for third_index, third in enumerate(thirds):
                if second == third or (second, third) not in values:
                    continue
                values[(second, third)] += probability / ((second_index + 1.0) * (third_index + 1.0))
    return _normalise(values)


def apply_conditional_placement(
    records: list[dict], scenarios: list[dict], history_rows
) -> dict:
    history = _history_pairs(history_rows)
    pairs: dict[int, dict[tuple[int, int], float]] = {}
    current_by_head: dict[int, dict[tuple[int, int], float]] = {}
    scenario_by_head: dict[int, dict[tuple[int, int], float]] = {}
    for head in range(1, 7):
        current = _current_pairs(records, head)
        scenario = _scenario_pairs(scenarios, head)
        current_by_head[head] = current
        scenario_by_head[head] = scenario
        pairs[head] = _normalise({
            key: HISTORY_WEIGHT * history[head][key]
            + CURRENT_WEIGHT * current[key]
            + SCENARIO_WEIGHT * scenario[key]
            for key in history[head]
        })

    p1 = {int(row["boat_no"]): float(row["win_prob"]) for row in records}
    second = {lane: 0.0 for lane in range(1, 7)}
    third = {lane: 0.0 for lane in range(1, 7)}
    for head, head_pairs in pairs.items():
        for (second_lane, third_lane), probability in head_pairs.items():
            joint = p1[head] * probability
            second[second_lane] += joint
            third[third_lane] += joint
    second = _normalise(second)
    third = _normalise(third)
    for row in records:
        lane = int(row["boat_no"])
        row["second_prob"] = round(second[lane], 6)
        row["third_prob"] = round(third[lane], 6)

    s16_pairs = _normalise({
        key: 0.60 * history[6][key]
        + 0.25 * current_by_head[6][key]
        + 0.15 * scenario_by_head[6][key]
        for key in history[6]
    })
    return {
        "pairs": pairs,
        "s16Pairs": s16_pairs,
        "historyRaceCount": HISTORY_RACE_COUNT,
        "weights": {"history": HISTORY_WEIGHT, "today": CURRENT_WEIGHT, "scenario": SCENARIO_WEIGHT},
        "shrinkSample": HISTORY_SHRINK_SAMPLE,
    }
