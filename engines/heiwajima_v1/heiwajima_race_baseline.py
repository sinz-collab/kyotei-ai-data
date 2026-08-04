from __future__ import annotations

import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARY_CSV = ROOT / "heiwajima_tide_summary_v1_0_N.csv"


def _num(value, default=0.0):
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _text(value):
    return str(value or "").strip()


def _normalize(values):
    total = sum(values) or 1.0
    return [float(value) / total for value in values]


def _race_time_band(race_no):
    race_no = int(race_no or 1)
    if race_no <= 4:
        return "early"
    if race_no <= 8:
        return "middle"
    return "late"


def _condition_label(tide):
    direction = _text(tide.get("tide_direction") or tide.get("direction")).lower()
    change = _num(
        tide.get("tide_change_cm_per_hour")
        or tide.get("change_cm_per_hour")
        or tide.get("tide_delta_cm"),
        0.0,
    )
    if "下げ" in direction or direction in ("falling", "ebb"):
        prefix = "falling"
    elif "上げ" in direction or direction in ("rising", "flood"):
        prefix = "rising"
    else:
        return ""

    magnitude = abs(change)
    if magnitude >= 20.0:
        suffix = "strong"
    elif magnitude >= 8.0:
        suffix = "mid"
    elif magnitude > 0.0:
        suffix = "weak"
    else:
        suffix = "very_weak"
    return f"{prefix}_{suffix}"


def _tide_window(tide):
    explicit = _text(tide.get("tide_window") or tide.get("nearest"))
    if explicit:
        return explicit

    minutes = _num(
        tide.get("minutes_to_extreme")
        or tide.get("minutes_to_nearest_extreme"),
        999.0,
    )
    phase = _text(tide.get("tide_phase") or tide.get("phase"))
    if minutes == 999.0:
        return ""

    target = "干潮" if "干潮" in phase else "満潮" if "満潮" in phase else ""
    if not target:
        return ""

    before = "前" in phase or minutes >= 0
    hours = 1 if abs(minutes) <= 75 else 2
    return f"{target}{'前' if before else '後'}{hours}h"


def _load_rows():
    if not SUMMARY_CSV.exists():
        return []
    with SUMMARY_CSV.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _expand_grouped_head(row, global_head):
    one = _num(row.get("one_escape_rate"))
    two = _num(row.get("course2_head_rate"))
    center = _num(row.get("center_head_3_4_rate"))
    outer = _num(row.get("outer_head_5_6_rate"))

    center_total = global_head[2] + global_head[3]
    outer_total = global_head[4] + global_head[5]
    c3_share = global_head[2] / center_total if center_total else 0.5
    c5_share = global_head[4] / outer_total if outer_total else 0.5

    return [
        one,
        two,
        center * c3_share,
        center * (1.0 - c3_share),
        outer * c5_share,
        outer * (1.0 - c5_share),
    ]


def race_specific_first_baseline(input_data, global_first_rates):
    """全体コース基礎と当該レースの条件別潮基礎を混合する。"""
    global_head = _normalize(global_first_rates)
    tide = input_data.get("tide") or {}
    race_no = int(input_data.get("race_no") or input_data.get("race", {}).get("race_no") or 1)
    time_band = _race_time_band(race_no)
    tide_type = _text(
        tide.get("tide_type_est")
        or tide.get("tideType")
        or tide.get("tide_type")
    )
    condition_label = _condition_label(tide)
    tide_window = _tide_window(tide)

    matched = []
    for row in _load_rows():
        summary_type = _text(row.get("summary_type"))
        if (
            summary_type == "潮種推定_時間帯"
            and _text(row.get("tide_type_est")) == tide_type
            and _text(row.get("race_time_band")) == time_band
        ):
            matched.append(("tide_type_time", row))
        elif (
            summary_type == "潮条件_時間帯"
            and _text(row.get("tide_condition_label")) == condition_label
            and _text(row.get("race_time_band")) == time_band
        ):
            matched.append(("tide_condition_time", row))
        elif (
            summary_type == "潮窓"
            and tide_window
            and _text(row.get("tide_window")) == tide_window
        ):
            matched.append(("tide_window", row))

    if not matched:
        return {
            "first_rates": global_head,
            "global_first_rates": global_head,
            "condition_first_rates": None,
            "condition_weight": 0.0,
            "effective_sample": 0,
            "sources": [],
            "time_band": time_band,
            "tide_type": tide_type,
            "condition_label": condition_label,
            "tide_window": tide_window,
        }

    sample_counts = [max(1, int(_num(row.get("races"), 1))) for _, row in matched]
    source_weights = [math.sqrt(count) for count in sample_counts]
    expanded = [_expand_grouped_head(row, global_head) for _, row in matched]
    total_weight = sum(source_weights) or 1.0

    condition_head = [
        sum(weight * rates[index] for weight, rates in zip(source_weights, expanded))
        / total_weight
        for index in range(6)
    ]
    condition_head = _normalize(condition_head)

    effective_n = min(sample_counts)
    condition_weight = min(0.65, max(0.30, effective_n / (effective_n + 250.0)))

    blended = _normalize([
        (1.0 - condition_weight) * global_rate
        + condition_weight * condition_rate
        for global_rate, condition_rate in zip(global_head, condition_head)
    ])

    return {
        "first_rates": blended,
        "global_first_rates": global_head,
        "condition_first_rates": condition_head,
        "condition_weight": round(condition_weight, 6),
        "effective_sample": effective_n,
        "sources": [
            {
                "kind": kind,
                "summary_type": row.get("summary_type"),
                "races": int(_num(row.get("races"), 0)),
            }
            for kind, row in matched
        ],
        "time_band": time_band,
        "tide_type": tide_type,
        "condition_label": condition_label,
        "tide_window": tide_window,
    }
