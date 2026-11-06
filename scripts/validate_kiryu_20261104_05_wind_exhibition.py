#!/usr/bin/env python3
"""Offline-only Kiryu validation for 2026-11-04/05.

This script does not mutate production data or engine coefficients.  It
reconstructs the frozen C+D joint distribution, applies predeclared LIVE
candidate transforms, and reruns the frozen 6+2+2 selector.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import subprocess
import sys
from collections import defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from kiryu_v1_engine import A, B, C, FEATURES, PERMS, KiryuV1, _num, _softmax  # noqa: E402


DATES = ("2026-11-04", "2026-11-05")
COEFFICIENTS = (0.05, 0.075, 0.10, 0.125, 0.15, 0.175, 0.20, 0.225)
GIT_SNAPSHOT = {"2026-11-04": "25aca8222", "2026-11-05": "HEAD"}
DOCS = ("direct", "exhibition", "original_exhibition", "result")
VPS_SNAPSHOT: Path | None = None


def git_json(revision: str, relative: str) -> dict:
    raw = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{revision}:{relative}"],
        stderr=subprocess.DEVNULL,
    )
    return json.loads(raw.decode("utf-8"))


def latest_complete_git_json(relative: str) -> dict | None:
    revisions = subprocess.check_output(
        ["git", "-C", str(ROOT), "log", "--format=%H", "--", relative],
        stderr=subprocess.DEVNULL,
    ).decode("ascii").splitlines()
    for revision in revisions:
        try:
            document = git_json(revision, relative)
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if document.get("complete") is True:
            document["_selected_from"] = f"git_history:{revision}"
            return document
    return None


def load_document(date: str, race: int, name: str) -> dict:
    relative = f"data/live/{date}/kiryu/{race:02d}/{name}.json"
    if VPS_SNAPSHOT is not None:
        path = VPS_SNAPSHOT / date / "kiryu" / f"{race:02d}" / f"{name}.json"
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            document["_selected_from"] = f"vps_snapshot:{path}"
            if document.get("complete") is True:
                return document
            recovered = latest_complete_git_json(relative)
            return recovered or document
    if date == "2026-11-05":
        path = ROOT / relative
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            document["_selected_from"] = f"working_tree:{path}"
            if document.get("complete") is True:
                return document
    document = git_json(GIT_SNAPSHOT[date], relative)
    document["_selected_from"] = f"git_snapshot:{GIT_SNAPSHOT[date]}"
    if document.get("complete") is True:
        return document
    return latest_complete_git_json(relative) or document


def load_venue(date: str) -> dict:
    path = ROOT / "data" / "venues" / "kiryu" / f"{date.replace('-', '')}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def baseline_joint(engine: KiryuV1, race: dict, date: str) -> np.ndarray:
    """Exact frozen joint calculation from KiryuV1.predict, without outputs."""
    X, racers = engine._matrix(race, date)
    entry_changed = any(
        int(row.get("entry_course") or row["lane"]) != int(row["lane"])
        for row in racers
    )
    p1 = engine._first(X, racers, entry_changed)
    p2, p3 = engine._conditionals(X)

    def centered(values, scale):
        values = np.asarray(values, float)
        return np.clip((values - values.mean()) / scale, -2, 2)

    ft2 = (
        0.5 * centered([_num(row.get("nat_2"), 0) for row in racers], 18)
        + 0.5 * centered([_num(row.get("local_2"), 0) for row in racers], 20)
        + 0.18 * centered(X[:, FEATURES.index("ath_second")], 0.16)
        + 0.18 * centered(X[:, FEATURES.index("course_second")], 0.15)
    )
    ft3 = (
        0.5 * centered([_num(row.get("nat_3"), 0) for row in racers], 22)
        + 0.5 * centered([_num(row.get("local_3"), 0) for row in racers], 23)
        + 0.18 * centered(X[:, FEATURES.index("ath_third")], 0.16)
        + 0.18 * centered(X[:, FEATURES.index("course_third")], 0.15)
    )
    p2n = p2.copy()
    for first in range(6):
        lanes = np.array([lane for lane in range(6) if lane != first])
        score = (
            np.log(np.maximum(p2[first, lanes], 1e-10))
            + 0.09 * ft2[lanes]
            + 0.09 * engine.hist2_pair[first, lanes]
        )
        p2n[first, lanes] = _softmax(score)
    z3 = np.log(np.maximum(p3, 1e-12)) + 0.075 * ft3[C] + 0.09 * engine.hist3
    p3n = np.zeros(120)
    for first in range(6):
        for second in range(6):
            indices = np.where((A == first) & (B == second))[0]
            if len(indices):
                p3n[indices] = _softmax(z3[indices])
    joint = p1[A] * p2n[A, B] * p3n
    return joint / joint.sum()


def select_622(engine: KiryuV1, joint: np.ndarray) -> list[int]:
    """Exact formal normal-D 6+2+2 selection from KiryuV1.predict."""
    ranked = np.argsort(-joint, kind="stable")
    selected = ranked[:6].tolist()
    for index in np.argsort(-(joint * np.exp(0.25 * engine.prior_rel)), kind="stable"):
        if int(index) not in selected:
            selected.append(int(index))
        if len(selected) >= 8:
            break
    scenario = selected[:6]
    for _ in range(4):
        used_heads = {A[index] for index in scenario}
        score = np.array(
            [joint[index] * (1.3 if A[index] not in used_heads else 1.0) for index in range(120)]
        )
        score[scenario] = -1
        scenario.append(int(np.argmax(score)))
    for index in scenario:
        if index not in selected:
            selected.append(index)
        if len(selected) >= 10:
            break
    for index in ranked:
        if int(index) not in selected:
            selected.append(int(index))
        if len(selected) >= 10:
            break
    return selected[:10]


def factorize(joint: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    p1 = np.array([joint[A == lane].sum() for lane in range(6)])
    p2 = np.zeros((6, 6))
    p3 = np.zeros((6, 6, 6))
    for first in range(6):
        for second in range(6):
            if first == second:
                continue
            pair = joint[(A == first) & (B == second)].sum()
            p2[first, second] = pair / max(p1[first], 1e-30)
            for third in range(6):
                if third in (first, second):
                    continue
                index = np.flatnonzero((A == first) & (B == second) & (C == third))[0]
                p3[first, second, third] = joint[index] / max(pair, 1e-30)
    return p1, p2, p3


def apply_scores(joint: np.ndarray, scores: tuple[np.ndarray, np.ndarray, np.ndarray]) -> np.ndarray:
    p1, p2, p3 = factorize(joint)
    q1 = _softmax(np.log(np.maximum(p1, 1e-30)) + scores[0])
    q2 = np.zeros_like(p2)
    q3 = np.zeros_like(p3)
    for first in range(6):
        allowed = np.array([lane for lane in range(6) if lane != first])
        q2[first, allowed] = _softmax(
            np.log(np.maximum(p2[first, allowed], 1e-30)) + scores[1][allowed]
        )
        for second in allowed:
            allowed3 = np.array([lane for lane in range(6) if lane not in (first, second)])
            q3[first, second, allowed3] = _softmax(
                np.log(np.maximum(p3[first, second, allowed3], 1e-30)) + scores[2][allowed3]
            )
    output = q1[A] * q2[A, B] * q3[A, B, C]
    return output / output.sum()


def lower_is_better(values: list[float]) -> np.ndarray:
    values = np.asarray(values, float)
    sd = max(float(values.std()), 0.025)
    return np.clip((values.mean() - values) / sd, -2.0, 2.0)


def combo(index: int) -> str:
    return "-".join(str(int(value) + 1) for value in PERMS[index])


def actual_index(order: list[str]) -> int:
    actual = tuple(int(value) - 1 for value in order[:3])
    return int(np.flatnonzero((PERMS == actual).all(axis=1))[0])


def source_row(date: str, race: int, documents: dict[str, dict]) -> dict:
    direct = documents["direct"]["data"]
    exhibit = documents["exhibition"]["data"]["entries"]
    original = documents["original_exhibition"]["data"]["entries"]
    result = documents["result"]["data"]
    complete = all(documents[name].get("complete") is True for name in DOCS)
    missing_fields = []
    for row in exhibit:
        for field in ("start_time", "exhibition_time", "exhibition_course"):
            if row.get(field) is None:
                missing_fields.append(f"lane{row.get('lane')}.{field}")
    for row in original:
        half_lap = row.get("half_lap_time", row.get("lap_time"))
        if half_lap is None:
            missing_fields.append(f"lane{row.get('lane')}.half_lap_time")
        for field in ("turn_time", "straight_time"):
            if row.get(field) is None:
                missing_fields.append(f"lane{row.get('lane')}.{field}")
    deadline = documents["direct"].get("deadline")
    cutoff = datetime.fromisoformat(f"{date}T{deadline}:00+09:00") if deadline else None
    prediction_fetches = [
        datetime.fromisoformat(documents[name]["fetched_at"])
        for name in ("direct", "exhibition", "original_exhibition")
        if documents[name].get("fetched_at")
    ]
    contemporaneous = bool(cutoff and len(prediction_fetches) == 3 and all(value <= cutoff for value in prediction_fetches))
    validation_ready = (
        len(exhibit) == 6
        and len(original) == 6
        and all(row.get("start_time") is not None and row.get("exhibition_time") is not None for row in exhibit)
        and all(row.get("turn_time") is not None and row.get("straight_time") is not None for row in original)
        and len(result.get("order") or []) >= 3
        and direct.get("wind_speed") is not None
        and direct.get("wind_direction") is not None
        and contemporaneous
    )
    return {
        "date": date,
        "race": race,
        "source_documents_complete": complete,
        "validation_ready": validation_ready,
        "prediction_inputs_pre_deadline": contemporaneous,
        "missing_fields": ";".join(missing_fields),
        "git_snapshot": GIT_SNAPSHOT[date],
        "direct_source": documents["direct"].get("source"),
        "direct_selected_from": documents["direct"].get("_selected_from"),
        "direct_fetched_at": documents["direct"].get("fetched_at"),
        "exhibition_source": documents["exhibition"].get("source"),
        "exhibition_selected_from": documents["exhibition"].get("_selected_from"),
        "exhibition_fetched_at": documents["exhibition"].get("fetched_at"),
        "original_source": documents["original_exhibition"].get("source"),
        "original_selected_from": documents["original_exhibition"].get("_selected_from"),
        "original_fetched_at": documents["original_exhibition"].get("fetched_at"),
        "result_source": documents["result"].get("source"),
        "result_selected_from": documents["result"].get("_selected_from"),
        "result_fetched_at": documents["result"].get("fetched_at"),
        "entry": "".join(str(value) for value in direct.get("actual_entry") or []),
        "entry_changed": (direct.get("actual_entry") or []) != list(range(1, 7)),
        "wind_direction_code": direct.get("wind_direction"),
        "wind_speed_mps": direct.get("wind_speed"),
        "wave_height_cm": direct.get("wave_height"),
        "stabilizer": direct.get("stabilizer"),
        "exhibition_st": ";".join(str(row.get("start_time")) for row in exhibit),
        "exhibition_time": ";".join(str(row.get("exhibition_time")) for row in exhibit),
        "half_lap_time": ";".join(str(row.get("half_lap_time", row.get("lap_time"))) for row in original),
        "turn_time": ";".join(str(row.get("turn_time")) for row in original),
        "straight_time": ";".join(str(row.get("straight_time")) for row in original),
        "result": "-".join(result.get("order") or []),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    global VPS_SNAPSHOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--vps-snapshot", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    VPS_SNAPSHOT = args.vps_snapshot

    engine = KiryuV1()
    details = []
    inventory = []
    missing = []
    sums = defaultdict(lambda: {"races": 0, "hits": 0, "baseline_hits": 0, "gained": 0, "lost": 0, "changed": 0})

    for date in DATES:
        payload = load_venue(date)
        race_map = {int(row["race"]): row for row in payload["races"]}
        for race_no in range(1, 13):
            try:
                documents = {name: load_document(date, race_no, name) for name in DOCS}
                inventory_row = source_row(date, race_no, documents)
                inventory.append(inventory_row)
                if not inventory_row["validation_ready"]:
                    raise ValueError("required fields incomplete or prediction input was not captured before deadline")
                result_order = documents["result"]["data"].get("order") or []
                if len(result_order) < 3:
                    raise ValueError("confirmed top-three result unavailable")
                race = deepcopy(race_map[race_no])
                # The venue payload is later updated with exhibition entry.
                # Morning C+D was generated from frame order, so restore only
                # these two mutable fields before reproducing the morning run.
                for racer in race.get("racers") or []:
                    racer["entry_course"] = int(racer["lane"])
                    racer["actual_course"] = int(racer["lane"])
                base = baseline_joint(engine, race, date)
                public_tickets = [
                    (row.get("combo") or row.get("combination")) if isinstance(row, dict) else str(row)
                    for row in race["predictionPre"]["tickets"]
                ]
                base_indices = select_622(engine, base)
                if [combo(index) for index in base_indices] != public_tickets:
                    raise ValueError("reconstructed 6+2+2 differs from stored morning tickets")
                truth = actual_index(result_order)
                base_hit = truth in base_indices
                direct = documents["direct"]["data"]
                original = sorted(documents["original_exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
                straight = lower_is_better([row["straight_time"] for row in original])
                turn = lower_is_better([row["turn_time"] for row in original])
                speed = float(direct["wind_speed"])
                direction = int(direct["wind_direction"])
                entry_changed = (direct.get("actual_entry") or []) != list(range(1, 7))
                angle = 2.0 * math.pi * (min(max(direction, 1), 16) - 1.0) / 16.0
                # Predeclared, outcome-free blend: direction chooses the balance
                # between straight and turn; wind speed scales the effect.
                straight_weight = (1.0 + math.cos(angle)) / 2.0
                turn_weight = 1.0 - straight_weight
                composite = min(speed, 5.0) / 2.0 * (
                    straight_weight * straight + turn_weight * turn
                )
                zero = np.zeros(6)
                candidates = {"baseline": base}
                if speed <= 2.0:
                    for coefficient in COEFFICIENTS:
                        for position, label in enumerate(("win", "second", "third")):
                            scores = [zero.copy(), zero.copy(), zero.copy()]
                            if not entry_changed:
                                scores[position] = coefficient * straight
                            candidates[f"straight_{label}_{coefficient:.3f}"] = apply_scores(base, tuple(scores))
                if speed < 6.0:
                    for coefficient in COEFFICIENTS:
                        scores = tuple(
                            coefficient * composite if not entry_changed else zero.copy()
                            for _ in range(3)
                        )
                        candidates[f"wind_direction_x_straight_turn_all_{coefficient:.3f}"] = apply_scores(base, scores)

                bucket = "0-2m" if speed <= 2 else ("3-5m" if speed < 6 else "6m+")
                for method, candidate in candidates.items():
                    tickets = select_622(engine, candidate)
                    hit = truth in tickets
                    key = (date, "all", method)
                    total_key = ("TOTAL", "all", method)
                    bucket_key = ("TOTAL", bucket, method)
                    for summary_key in (key, total_key, bucket_key):
                        row = sums[summary_key]
                        row["races"] += 1
                        row["hits"] += int(hit)
                        row["baseline_hits"] += int(base_hit)
                        row["gained"] += int(hit and not base_hit)
                        row["lost"] += int(base_hit and not hit)
                        row["changed"] += int(tickets != base_indices)
                    details.append({
                        "date": date,
                        "race": race_no,
                        "wind_bucket": bucket,
                        "wind_direction_code": direction,
                        "wind_speed_mps": speed,
                        "wave_height_cm": direct.get("wave_height"),
                        "stabilizer": direct.get("stabilizer"),
                        "method": method,
                        "result": combo(truth),
                        "hit": hit,
                        "baseline_hit": base_hit,
                        "gained": hit and not base_hit,
                        "lost": base_hit and not hit,
                        "morning_tickets": ";".join(combo(index) for index in base_indices),
                        "candidate_tickets": ";".join(combo(index) for index in tickets),
                        "actual_probability_before": float(base[truth]),
                        "actual_probability_after": float(candidate[truth]),
                        "probability_sum": float(candidate.sum()),
                    })
            except Exception as exc:  # explicit audit output; do not invent values
                missing.append({"date": date, "race": race_no, "reason": str(exc)})

    summary = []
    for (date, bucket, method), values in sorted(sums.items()):
        summary.append({
            "date": date,
            "wind_bucket": bucket,
            "method": method,
            **values,
            "hit_rate": values["hits"] / values["races"] if values["races"] else None,
            "delta_hits": values["hits"] - values["baseline_hits"],
        })

    write_csv(args.output_dir / "source_inventory.csv", inventory)
    write_csv(args.output_dir / "ticket_details.csv", details)
    write_csv(args.output_dir / "method_summary.csv", summary)
    write_csv(args.output_dir / "unverified_races.csv", missing)
    audit = {
        "dates": list(DATES),
        "requested_races": 24,
        "verified_races": 24 - len(missing),
        "unverified_races": missing,
        "baseline": "frozen Kiryu v1: C improved + variable escape; D power 0.15; formal 6+2+2",
        "baseline_internal_coefficients": [0.09, 0.075, 0.09, 0.09],
        "coefficient_grid_predeclared": list(COEFFICIENTS),
        "straight_scope": "wind speed 0-2m only; one finishing position at a time",
        "composite_formula": "min(wind_speed,5)/2 * (((1+cos(direction_angle))/2)*straight_z + ((1-cos(direction_angle))/2)*turn_z)",
        "composite_scope": "wind speed below 6m; same coefficient at all three conditional positions",
        "six_m_plus_policy": "separate bucket; no candidate correction; stabilizer value reported without inference",
        "target_results_used_for": ["hit scoring only"],
        "target_results_used_for_coefficient_selection": False,
        "production_files_modified": False,
        "probability_sum_max_error": max(abs(row["probability_sum"] - 1.0) for row in details) if details else None,
    }
    (args.output_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
