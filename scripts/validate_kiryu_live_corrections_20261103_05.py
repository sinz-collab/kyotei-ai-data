#!/usr/bin/env python3
"""Reproduce and compare Kiryu LIVE corrections for 2026-11-03..05.

Offline validation only.  This script never writes production data.  It reuses
the frozen Kiryu v1 C+D joint calculation, formal 6+2+2 selector, and the
2026-11-04/05 wind/exhibition transforms instead of reimplementing them.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from copy import deepcopy
from pathlib import Path

import numpy as np

from build_kiryu_slit_validation_package import (
    DATES,
    GIT_1103,
    VPS,
    deadline_at,
    expected_1103_tickets,
    is_predeadline,
    load_doc,
    load_venue,
    model_races_1103,
)
from validate_kiryu_20261104_05_wind_exhibition import (
    A,
    B,
    C,
    PERMS,
    KiryuV1,
    apply_scores,
    baseline_joint,
    combo,
    factorize,
    lower_is_better,
    select_622,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "validation" / "kiryu_live_corrections_20261103_05"
EXCLUDED = {("2026-11-04", 1)}
WIND_CANDIDATES = (
    ("straight_second_0.100", "straight_second", 0.100),
    ("straight_second_0.125", "straight_second", 0.125),
    ("straight_second_0.150", "straight_second", 0.150),
    ("composite_all_0.100", "composite_all", 0.100),
    ("composite_all_0.125", "composite_all", 0.125),
    ("composite_all_0.150", "composite_all", 0.150),
    ("composite_all_0.175", "composite_all", 0.175),
)
ST_SCHEMES = {
    "national100": (1.0, 0.0, 0.0),
    "national80_exhibition20": (0.8, 0.0, 0.2),
    "national60_local20_exhibition20": (0.6, 0.2, 0.2),
    "national40_local40_exhibition20": (0.4, 0.4, 0.2),
}
ST_COEFFICIENTS = (0.05, 0.10, 0.15)
FINAL_METHODS = ("baseline", "left_st_only", "wind_only", "wind_then_left_st")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"no rows for {path.name}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def number(value) -> float | None:
    if value in (None, "", "-", "None"):
        return None
    try:
        parsed = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def imputed_st(racers: list[dict], exhibit: list[dict], weights: tuple[float, float, float]) -> np.ndarray:
    """Specified national/local/exhibition blend with neutral fallbacks."""
    national = [number(row.get("avg_st")) for row in racers]
    valid_national = [value for value in national if value is not None]
    if not valid_national:
        raise ValueError("all national average ST values are missing")
    neutral = float(np.mean(valid_national))
    national = np.asarray([neutral if value is None else value for value in national], float)
    local = np.asarray([
        national[index]
        if number(row.get("boaters_local_avg_st") or row.get("local_st")) is None
        else number(row.get("boaters_local_avg_st") or row.get("local_st"))
        for index, row in enumerate(racers)
    ], float)
    exhibition_by_lane = {int(row["lane"]): number(row.get("start_time")) for row in exhibit}
    exhibition = np.asarray([
        national[index]
        if exhibition_by_lane.get(int(row["lane"])) is None
        else exhibition_by_lane[int(row["lane"])]
        for index, row in enumerate(racers)
    ], float)
    return weights[0] * national + weights[1] * local + weights[2] * exhibition


def left_st_features(st: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    wall = np.zeros(6)
    deep = np.zeros(6)
    attack = np.zeros(6)
    for lane in range(1, 6):
        wall[lane] = np.clip((st[lane - 1] - st[lane]) / 0.04, -1.5, 1.5)
    for lane in range(2, 6):
        deep[lane] = np.clip((st[lane - 2] - st[lane]) / 0.06, -1.5, 1.5)
    for lane in range(2, 5):
        attack[lane] = np.clip(
            0.7 * max(wall[lane], 0.0) + 0.3 * max(deep[lane], 0.0), 0.0, 1.5
        )
    return wall, deep, attack


def apply_left_st(joint: np.ndarray, wall: np.ndarray, coefficient: float, entry_changed: bool) -> np.ndarray:
    if entry_changed:
        return joint.copy()
    zero = np.zeros(6)
    output = apply_scores(joint, (zero, coefficient * wall, zero))
    before = factorize(joint)
    after = factorize(output)
    if not np.allclose(before[0], after[0], atol=1e-14, rtol=1e-12):
        raise AssertionError("left-ST correction changed first-place probabilities")
    if not np.allclose(before[2], after[2], atol=1e-14, rtol=1e-12):
        raise AssertionError("left-ST correction changed third-place conditional probabilities")
    return output


def wind_features(direct: dict, original: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    straight_values = [number(row.get("straight_time")) for row in original]
    turn_values = [number(row.get("turn_time")) for row in original]
    if any(value is None for value in straight_values + turn_values):
        raise ValueError("wind correction input missing straight or turn time")
    straight = lower_is_better(straight_values)
    turn = lower_is_better(turn_values)
    speed = number(direct.get("wind_speed"))
    direction = number(direct.get("wind_direction"))
    if speed is None or direction is None:
        raise ValueError("wind correction input missing speed or direction")
    angle = 2.0 * math.pi * (min(max(int(direction), 1), 16) - 1.0) / 16.0
    straight_weight = (1.0 + math.cos(angle)) / 2.0
    composite = min(speed, 5.0) / 2.0 * (
        straight_weight * straight + (1.0 - straight_weight) * turn
    )
    return straight, composite


def apply_wind(
    joint: np.ndarray,
    direct: dict,
    original: list[dict],
    kind: str,
    coefficient: float,
    entry_changed: bool,
) -> np.ndarray:
    if entry_changed:
        return joint.copy()
    speed = number(direct.get("wind_speed"))
    if speed is None:
        raise ValueError("wind speed missing")
    straight, composite = wind_features(direct, original)
    zero = np.zeros(6)
    if kind == "straight_second":
        if speed > 2.0:
            return joint.copy()
        return apply_scores(joint, (zero, coefficient * straight, zero))
    if kind == "composite_all":
        if speed >= 6.0:
            return joint.copy()
        score = coefficient * composite
        return apply_scores(joint, (score, score, score))
    raise ValueError(f"unknown wind candidate {kind}")


def actual_index(order: list[str]) -> int:
    actual = tuple(int(value) - 1 for value in order[:3])
    indices = np.flatnonzero((PERMS == actual).all(axis=1))
    if len(indices) != 1:
        raise ValueError(f"invalid result order {order}")
    return int(indices[0])


def marginals(joint: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return tuple(np.asarray([joint[position == lane].sum() for lane in range(6)]) for position in (A, B, C))


def metric_values(joint: np.ndarray, truth: int) -> dict[str, float]:
    p1, p2, p3 = factorize(joint)
    first, second, third = (int(value) for value in PERMS[truth])
    onehot = np.zeros(120)
    onehot[truth] = 1.0
    second_target = np.zeros(6)
    second_target[second] = 1.0
    third_target = np.zeros(6)
    third_target[third] = 1.0
    return {
        "trifecta_logloss": -math.log(max(float(joint[truth]), 1e-30)),
        "trifecta_brier": float(np.square(joint - onehot).sum()),
        "second_conditional_logloss": -math.log(max(float(p2[first, second]), 1e-30)),
        "second_conditional_brier": float(np.square(p2[first] - second_target).sum()),
        "third_conditional_logloss": -math.log(max(float(p3[first, second, third]), 1e-30)),
        "third_conditional_brier": float(np.square(p3[first, second] - third_target).sum()),
    }


def aggregate_metrics(rows: list[dict], methods: list[str], scope_name: str, predicate) -> list[dict]:
    output = []
    for method in methods:
        selected = [row for row in rows if row["method"] == method and predicate(row)]
        if not selected:
            continue
        output.append({
            "scope": scope_name,
            "method": method,
            "races": len(selected),
            "formal_622_hits": sum(int(row["formal_622_hit"]) for row in selected),
            "top10_hits": sum(int(row["top10_hit"]) for row in selected),
            **{
                key: float(np.mean([row[key] for row in selected]))
                for key in (
                    "trifecta_logloss",
                    "trifecta_brier",
                    "second_conditional_logloss",
                    "second_conditional_brier",
                    "third_conditional_logloss",
                    "third_conditional_brier",
                )
            },
        })
    return output


def stable_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)

    engine = KiryuV1()
    expected3 = expected_1103_tickets()
    races = []
    source_rows = []
    feature_rows = []
    baseline_reproduction = []

    for date in DATES:
        venue = load_venue(date)
        race_map = {int(row["race"]): row for row in venue["races"]}
        replay3 = model_races_1103(venue) if date == "2026-11-03" else None
        for race_no in range(1, 13):
            race_id = f"{date.replace('-', '')}_KIRYU_{race_no:02d}R"
            race = deepcopy(race_map[race_no])
            docs = {name: load_doc(date, race_no, name) for name in ("direct", "exhibition", "original_exhibition", "result")}
            deadline = race.get("deadline") or docs["direct"].get("deadline")
            cutoff = deadline_at(date, deadline)
            predeadline = all(is_predeadline(docs[name], cutoff) for name in ("direct", "exhibition", "original_exhibition"))
            excluded = (date, race_no) in EXCLUDED
            direct = docs["direct"]["data"]
            exhibit = sorted(docs["exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            original = sorted(docs["original_exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            order = docs["result"]["data"].get("order") or []
            if len(order) < 3:
                raise ValueError(f"{race_id}: result unavailable")
            actual_entry = direct.get("actual_entry") or list(range(1, 7))
            entry_changed = actual_entry != list(range(1, 7))

            model_race = deepcopy(replay3[race_no]) if replay3 is not None else deepcopy(race)
            if replay3 is None:
                for racer in model_race.get("racers") or []:
                    racer["entry_course"] = int(racer["lane"])
                    racer["actual_course"] = int(racer["lane"])
            base = baseline_joint(engine, model_race, date)
            formal = select_622(engine, base)
            formal_combos = [combo(index) for index in formal]
            expected = expected3[race_no] if date == "2026-11-03" else [
                (row.get("combo") or row.get("combination")) if isinstance(row, dict) else str(row)
                for row in race["predictionPre"]["tickets"]
            ]
            match = formal_combos == expected
            baseline_reproduction.append(match)
            if not match:
                raise AssertionError(f"{race_id}: baseline 6+2+2 mismatch")

            st_racers = sorted(race.get("racers") or [], key=lambda row: int(row["lane"]))
            st_features = {}
            for scheme, weights in ST_SCHEMES.items():
                st = imputed_st(st_racers, exhibit, weights)
                wall, deep, attack = left_st_features(st)
                st_features[scheme] = (st, wall, deep, attack)
                for lane in range(6):
                    feature_rows.append({
                        "race_id": race_id,
                        "scheme": scheme,
                        "lane": lane + 1,
                        "blended_st": f"{st[lane]:.17g}",
                        "wall": f"{wall[lane]:.17g}",
                        "deep": f"{deep[lane]:.17g}",
                        "attack": f"{attack[lane]:.17g}",
                    })

            truth = actual_index(order)
            wind = {
                name: apply_wind(base, direct, original, kind, coefficient, entry_changed)
                for name, kind, coefficient in WIND_CANDIDATES
            }
            left = {
                f"left_{scheme}_{coefficient:.3f}": apply_left_st(base, st_features[scheme][1], coefficient, entry_changed)
                for scheme in ST_SCHEMES
                for coefficient in ST_COEFFICIENTS
            }
            races.append({
                "race_id": race_id,
                "date": date,
                "race": race_no,
                "excluded": excluded,
                "predeadline": predeadline,
                "entry_changed": entry_changed,
                "actual_entry": actual_entry,
                "truth": truth,
                "result": combo(truth),
                "base": base,
                "wind": wind,
                "left": left,
                "main_wall": st_features["national100"][1],
                "direct": direct,
                "original": original,
            })
            for name, doc in docs.items():
                source_rows.append({
                    "race_id": race_id,
                    "source_type": name,
                    "source_url": doc.get("source"),
                    "selected_from": doc.get("_selected_from"),
                    "fetched_at": doc.get("fetched_at"),
                    "deadline_jst": f"{date}T{deadline}:00+09:00",
                    "captured_predeadline": "not_applicable_result" if name == "result" else is_predeadline(doc, cutoff),
                    "excluded": excluded,
                })

    if len(races) != 36 or not all(baseline_reproduction):
        raise AssertionError("baseline reproduction gate failed")

    candidate_rows = []
    for race in races:
        methods = {"baseline": race["base"], **race["wind"], **race["left"]}
        for method, joint in methods.items():
            if abs(float(joint.sum()) - 1.0) > 1e-12:
                raise AssertionError(f"{race['race_id']} {method}: probability normalization failed")
            formal = select_622(engine, joint)
            top10 = np.argsort(-joint, kind="stable")[:10].tolist()
            candidate_rows.append({
                "race_id": race["race_id"],
                "date": race["date"],
                "race": race["race"],
                "method": method,
                "excluded": race["excluded"],
                "predeadline": race["predeadline"],
                "entry_changed": race["entry_changed"],
                "result": race["result"],
                "formal_622_hit": race["truth"] in formal,
                "top10_hit": race["truth"] in top10,
                **metric_values(joint, race["truth"]),
            })

    wind_names = [name for name, _, _ in WIND_CANDIDATES]
    strict_23 = lambda row: row["date"] in ("2026-11-04", "2026-11-05") and row["predeadline"] and not row["excluded"]
    candidate_metrics = []
    candidate_metrics += aggregate_metrics(candidate_rows, ["baseline"] + wind_names, "wind_strict_predeadline_23", strict_23)
    candidate_metrics += aggregate_metrics(
        candidate_rows,
        ["baseline"] + wind_names,
        "wind_strict_predeadline_normal_entry",
        lambda row: strict_23(row) and not row["entry_changed"],
    )
    left_names = [f"left_{scheme}_{coefficient:.3f}" for scheme in ST_SCHEMES for coefficient in ST_COEFFICIENTS]
    candidate_metrics += aggregate_metrics(
        candidate_rows,
        ["baseline"] + left_names,
        "left_all_35",
        lambda row: not row["excluded"],
    )
    candidate_metrics += aggregate_metrics(
        candidate_rows,
        ["baseline"] + left_names,
        "left_normal_entry_29",
        lambda row: not row["excluded"] and not row["entry_changed"],
    )

    strict_wind = [row for row in candidate_metrics if row["scope"] == "wind_strict_predeadline_23" and row["method"] != "baseline"]
    expected_hits = {
        "straight_second_0.100": 13,
        "straight_second_0.125": 13,
        "straight_second_0.150": 13,
        "composite_all_0.100": 13,
        "composite_all_0.125": 13,
        "composite_all_0.150": 13,
        "composite_all_0.175": 13,
    }
    for row in strict_wind:
        if row["races"] != 23 or row["formal_622_hits"] != expected_hits[row["method"]]:
            raise AssertionError(f"existing wind 13/23 reproduction failed: {row}")

    selection_order = ("trifecta_logloss", "trifecta_brier", "second_conditional_logloss")
    ranked_wind = sorted(strict_wind, key=lambda row: tuple(row[key] for key in selection_order))
    selected_wind = ranked_wind[0]
    if len(ranked_wind) > 1 and all(
        abs(selected_wind[key] - ranked_wind[1][key]) <= 1e-12 for key in selection_order
    ):
        raise RuntimeError("wind candidate metrics remain tied; production implementation forbidden")
    selected_name = selected_wind["method"]
    selected_kind, selected_coefficient = next((kind, coefficient) for name, kind, coefficient in WIND_CANDIDATES if name == selected_name)

    final_probability_rows = []
    marginal_rows = []
    ticket_rows = []
    final_race_rows = []
    for race in races:
        wind_joint = race["wind"][selected_name]
        left_joint = apply_left_st(race["base"], race["main_wall"], 0.10, race["entry_changed"])
        combined = apply_left_st(wind_joint, race["main_wall"], 0.10, race["entry_changed"])
        final = {
            "baseline": race["base"],
            "left_st_only": left_joint,
            "wind_only": wind_joint,
            "wind_then_left_st": combined,
        }
        for method, joint in final.items():
            if abs(float(joint.sum()) - 1.0) > 1e-12:
                raise AssertionError(f"{race['race_id']} {method}: final normalization failed")
            formal = select_622(engine, joint)
            top10 = np.argsort(-joint, kind="stable")[:10].tolist()
            values = metric_values(joint, race["truth"])
            final_race_rows.append({
                "race_id": race["race_id"], "date": race["date"], "race": race["race"],
                "method": method, "excluded": race["excluded"], "predeadline": race["predeadline"],
                "entry_changed": race["entry_changed"], "result": race["result"],
                "formal_622_hit": race["truth"] in formal, "top10_hit": race["truth"] in top10,
                **values,
            })
            for index in range(120):
                final_probability_rows.append({
                    "race_id": race["race_id"], "method": method, "combination": combo(index),
                    "probability": f"{float(joint[index]):.17g}",
                })
            for position, probabilities in enumerate(marginals(joint), 1):
                for lane in range(6):
                    marginal_rows.append({
                        "race_id": race["race_id"], "method": method, "lane": lane + 1,
                        "position": position, "probability": f"{float(probabilities[lane]):.17g}",
                    })
            for selector, indices in (("formal_6+2+2", formal), ("simple_top10", top10)):
                for rank, index in enumerate(indices, 1):
                    ticket_rows.append({
                        "race_id": race["race_id"], "method": method, "selector": selector,
                        "rank": rank, "segment": (
                            "probability_top6" if selector == "formal_6+2+2" and rank <= 6
                            else "history_next2" if selector == "formal_6+2+2" and rank <= 8
                            else "scenario_next2" if selector == "formal_6+2+2"
                            else "probability_rank"
                        ),
                        "combination": combo(index), "probability": f"{float(joint[index]):.17g}",
                    })

    final_metrics = []
    for scope, predicate in (
        ("all_35", lambda row: not row["excluded"]),
        ("normal_entry_29", lambda row: not row["excluded"] and not row["entry_changed"]),
        ("entry_changed_6", lambda row: not row["excluded"] and row["entry_changed"]),
        ("strict_predeadline_23", strict_23),
    ):
        final_metrics += aggregate_metrics(final_race_rows, list(FINAL_METHODS), scope, predicate)

    write_csv(out / "candidate_metrics.csv", candidate_metrics)
    write_csv(out / "race_results.csv", final_race_rows)
    write_csv(out / "probabilities_120.csv", final_probability_rows)
    write_csv(out / "marginals.csv", marginal_rows)
    write_csv(out / "tickets.csv", ticket_rows)
    write_csv(out / "left_st_features.csv", feature_rows)
    write_csv(out / "sources.csv", source_rows)
    write_csv(out / "final_metrics.csv", final_metrics)

    conditions = {
        "dates": list(DATES),
        "excluded_races": ["20261104_KIRYU_01R"],
        "baseline": "frozen C improved + variable escape; normal D power 0.15; exact formal 6+2+2",
        "baseline_reproduction": "36/36 formal tickets",
        "wind_source": "validate_kiryu_20261104_05_wind_exhibition.py functions reused",
        "wind_candidates": [name for name, _, _ in WIND_CANDIDATES],
        "wind_selection_scope": "2026-11-04/05 strict pre-deadline 23 races; entry-change corrections neutralized",
        "wind_selection_order": list(selection_order),
        "selected_wind": {
            "name": selected_name,
            "kind": selected_kind,
            "coefficient": selected_coefficient,
            **{key: selected_wind[key] for key in selection_order},
        },
        "left_st_main": "national100; wall; second-place conditional; coefficient 0.10",
        "left_st_missing": "missing national=race valid national mean; missing local/exhibition=national",
        "application_order": ["wind", "left_st"],
        "entry_change_policy": "both corrections disabled",
        "outcome_use": "results are used only for metric evaluation and requested wind-candidate selection; never probability features",
        "production_modified": False,
        "source_limits": f"2026-11-03 documents come from Git {GIT_1103} and are retrospective; 2026-11-04/05 use preserved VPS snapshot {VPS}",
    }
    (out / "conditions.json").write_text(json.dumps(conditions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    hashes = {
        path.name: stable_hash(path)
        for path in sorted(out.iterdir())
        if path.is_file() and path.name not in ("hashes.json", "REPORT.md")
    }
    (out / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def markdown_table(rows: list[dict], columns: list[str]) -> str:
        head = "| " + " | ".join(columns) + " |"
        rule = "|" + "|".join("---" for _ in columns) + "|"
        body = ["| " + " | ".join(str(row[column]) for column in columns) + " |" for row in rows]
        return "\n".join([head, rule] + body)

    report_wind = [{
        "method": row["method"], "hits_622": f"{row['formal_622_hits']}/23", "hits_top10": f"{row['top10_hits']}/23",
        "trifecta_LL": f"{row['trifecta_logloss']:.9f}", "Brier120": f"{row['trifecta_brier']:.9f}",
        "second_LL": f"{row['second_conditional_logloss']:.9f}",
    } for row in [next(row for row in candidate_metrics if row["scope"] == "wind_strict_predeadline_23" and row["method"] == name) for name in ["baseline"] + wind_names]]
    report_final = [{
        "scope": row["scope"], "method": row["method"], "races": row["races"],
        "hits_622": row["formal_622_hits"], "hits_top10": row["top10_hits"],
        "trifecta_LL": f"{row['trifecta_logloss']:.9f}", "Brier120": f"{row['trifecta_brier']:.9f}",
    } for row in final_metrics]
    report = f"""# Kiryu LIVE correction integration validation (2026-11-03..05)

## Gates

- Frozen morning C+D formal 6+2+2 reproduction: **36/36 exact**.
- 2026-11-04 R1 is excluded; entry changes are reported separately and both corrections are neutralized.
- All final 120-way distributions are normalized and all requested outputs are written as CSV.
- Production engine/data/site files are not modified by this validation.

## Wind candidate selection (strict pre-deadline 23 races)

{markdown_table(report_wind, ['method', 'hits_622', 'hits_top10', 'trifecta_LL', 'Brier120', 'second_LL'])}

Predeclared lexicographic rule: trifecta logloss, then 120-class Brier, then second-place conditional logloss. Selected: **{selected_name}**.

## Integrated four-way comparison

{markdown_table(report_final, ['scope', 'method', 'races', 'hits_622', 'hits_top10', 'trifecta_LL', 'Brier120'])}

Formal 6+2+2 and simple probability top-10 are deliberately reported in separate columns. Detailed ranked tickets are in `tickets.csv`.

## Provenance limitation

The 2026-11-03 input documents are retrospective Git artifacts and lack pre-deadline capture proof. They are included because the requested range is November 3-5, but they are not relabeled as contemporaneous LIVE data. November 4-5 retain the existing timestamp audit; November 4 R1 remains excluded.
"""
    (out / "REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({
        "output": str(out),
        "baseline_reproduction": "36/36",
        "selected_wind": selected_name,
        "selected_metrics": {key: selected_wind[key] for key in selection_order},
        "files": sorted(path.name for path in out.iterdir() if path.is_file()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
