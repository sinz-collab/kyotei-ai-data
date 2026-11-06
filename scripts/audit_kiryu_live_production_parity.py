#!/usr/bin/env python3
"""Compare frozen validation transforms with the production Kiryu LIVE path."""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

import numpy as np

from build_kiryu_slit_validation_package import (
    DATES,
    deadline_at,
    is_predeadline,
    load_doc,
    load_venue,
    model_races_1103,
)
from validate_kiryu_20261104_05_wind_exhibition import (
    A,
    B,
    C,
    KiryuV1,
    baseline_joint,
    combo,
    select_622,
)
from validate_kiryu_live_corrections_20261103_05 import (
    EXCLUDED,
    apply_left_st,
    apply_wind,
    imputed_st,
    left_st_features,
)

from kiryu_live_corrections import apply_live_corrections
from run_kiryu_v1 import build_live_calculation_input


ROOT = Path(__file__).resolve().parents[1]


def marginals(joint):
    return np.concatenate([
        np.asarray([joint[position == lane].sum() for lane in range(6)])
        for position in (A, B, C)
    ])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "validation" / "kiryu_live_corrections_20261103_05" / "production_parity.json",
    )
    args = parser.parse_args()
    engine = KiryuV1()
    rows = []
    validation_hits = []
    production_hits = []

    for date in DATES:
        venue = load_venue(date)
        race_map = {int(row["race"]): row for row in venue["races"]}
        replay3 = model_races_1103(venue) if date == "2026-11-03" else None
        for race_no in range(1, 13):
            race = deepcopy(race_map[race_no])
            docs = {name: load_doc(date, race_no, name) for name in ("direct", "exhibition", "original_exhibition", "result")}
            direct = docs["direct"]["data"]
            exhibit = sorted(docs["exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            original = sorted(docs["original_exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            result = docs["result"]["data"].get("order") or []
            actual_entry = direct.get("actual_entry") or list(range(1, 7))
            entry_changed = actual_entry != list(range(1, 7))

            model_race = deepcopy(replay3[race_no]) if replay3 is not None else deepcopy(race)
            for racer in model_race.get("racers") or []:
                racer["entry_course"] = int(racer["lane"])
                racer["actual_course"] = int(racer["lane"])
            validation_base = baseline_joint(engine, model_race, date)
            production_base = engine.normal_joint(model_race, date)

            st_racers = sorted(race.get("racers") or [], key=lambda row: int(row["lane"]))
            st = imputed_st(st_racers, exhibit, (1.0, 0.0, 0.0))
            wall = left_st_features(st)[0]
            validation_after_wind = apply_wind(
                validation_base, direct, original, "straight_second", 0.150, entry_changed
            )
            validation_final = apply_left_st(validation_after_wind, wall, 0.100, entry_changed)

            production_docs = {name: docs[name] for name in ("direct", "exhibition", "original_exhibition")}
            input_gate_passed = True
            try:
                live_input, _ = build_live_calculation_input(production_docs, st_racers)
            except RuntimeError:
                if date != "2026-11-03":
                    raise
                # Nov-03 is a retrospective artifact whose documents were not
                # saved with the production completeness marker.  Compare the
                # pure transform, but do not claim that it passed the LIVE gate.
                input_gate_passed = False
                live_input = {
                    "actual_entry": actual_entry,
                    "boats": [
                        {"lane": int(row["lane"]), "straight_time": row.get("straight_time")}
                        for row in original
                    ],
                    "weather": {"wind_speed": direct.get("wind_speed")},
                }
            production_final, audit = apply_live_corrections(production_base, st_racers, live_input)

            truth_combo = "-".join(str(value) for value in result[:3])
            validation_tickets = [combo(index) for index in select_622(engine, validation_final)]
            production_tickets = [combo(index) for index in engine.select_normal_indices(production_final)]
            excluded = (date, race_no) in EXCLUDED
            deadline = race.get("deadline") or docs["direct"].get("deadline")
            cutoff = deadline_at(date, deadline)
            predeadline = all(is_predeadline(docs[name], cutoff) for name in ("direct", "exhibition", "original_exhibition"))
            row = {
                "race_id": f"{date.replace('-', '')}_KIRYU_{race_no:02d}R",
                "excluded": excluded,
                "predeadline": predeadline,
                "entry_changed": entry_changed,
                "production_input_gate_passed": input_gate_passed,
                "base_120_max_abs_error": float(np.max(np.abs(validation_base - production_base))),
                "corrected_120_max_abs_error": float(np.max(np.abs(validation_final - production_final))),
                "marginal_18_max_abs_error": float(np.max(np.abs(marginals(validation_final) - marginals(production_final)))),
                "tickets_and_order_match": validation_tickets == production_tickets,
                "validation_hit": truth_combo in validation_tickets,
                "production_hit": truth_combo in production_tickets,
                "wind_applied_match": audit["windStraight"]["applied"] == (not entry_changed and float(direct["wind_speed"]) <= 2.0),
                "left_st_applied_match": audit["leftNeighborSt"]["applied"] == (not entry_changed),
                "stop_match": audit["stoppedForEntryChange"] == entry_changed,
            }
            rows.append(row)
            if not excluded:
                validation_hits.append(row["validation_hit"])
                production_hits.append(row["production_hit"])

    failures = [
        row for row in rows
        if row["base_120_max_abs_error"] > 1e-14
        or row["corrected_120_max_abs_error"] > 1e-14
        or row["marginal_18_max_abs_error"] > 1e-14
        or not row["tickets_and_order_match"]
        or row["validation_hit"] != row["production_hit"]
        or not row["wind_applied_match"]
        or not row["left_st_applied_match"]
        or not row["stop_match"]
    ]
    output = {
        "races": len(rows),
        "excluded": [row["race_id"] for row in rows if row["excluded"]],
        "production_validation_parity": not failures,
        "max_base_120_error": max(row["base_120_max_abs_error"] for row in rows),
        "max_corrected_120_error": max(row["corrected_120_max_abs_error"] for row in rows),
        "max_marginal_18_error": max(row["marginal_18_max_abs_error"] for row in rows),
        "formal_622_hits_validation": sum(validation_hits),
        "formal_622_hits_production": sum(production_hits),
        "failures": failures,
        "race_checks": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "race_checks"}, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
