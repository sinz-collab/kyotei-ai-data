#!/usr/bin/env python3
"""Build the audited 2026-11-03..05 Kiryu slit-validation handoff.

This is packaging glue only.  It reuses the frozen Kiryu v1 engine and the
existing offline validation transforms; it does not fetch or modify production.
"""
from __future__ import annotations

import csv
import json
import math
import subprocess
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import numpy as np

from validate_kiryu_20261104_05_wind_exhibition import (
    A,
    B,
    C,
    PERMS,
    KiryuV1,
    apply_scores,
    baseline_joint,
    combo,
    lower_is_better,
    select_622,
)


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
OUTDIR = ROOT / "validation" / "kiryu_slit_validation_20261103_05"
ZIP_PATH = WORKSPACE / "kiryu_slit_validation_20261103_05.zip"
VPS = ROOT / "validation" / "kiryu_wind_exhibition_20261104_05" / "vps_snapshot"
FIXTURES = ROOT / "tests" / "fixtures" / "kiryu_v1"
DATES = ("2026-11-03", "2026-11-04", "2026-11-05")
GIT_1103 = "ebe872e6d"
CORRECTION_COEFFICIENT = 0.100


def git_json(revision: str, relative: str) -> dict:
    raw = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{revision}:{relative}"])
    value = json.loads(raw.decode("utf-8"))
    value["_selected_from"] = f"git:{revision}:{relative}"
    return value


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["_selected_from"] = f"local:{path.relative_to(WORKSPACE)}"
    return value


def load_doc(date: str, race: int, name: str) -> dict:
    relative = f"data/live/{date}/kiryu/{race:02d}/{name}.json"
    if date == "2026-11-03":
        return git_json(GIT_1103, relative)
    return read_json(VPS / date / "kiryu" / f"{race:02d}" / f"{name}.json")


def load_venue(date: str) -> dict:
    return json.loads((ROOT / "data" / "venues" / "kiryu" / f"{date.replace('-', '')}.json").read_text(encoding="utf-8"))


def write_csv(name: str, rows: list[dict]) -> None:
    path = OUTDIR / name
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def parse_time(value):
    if value in (None, "", "None"):
        return None
    return float(value)


def deadline_at(date: str, deadline: str | None):
    return datetime.fromisoformat(f"{date}T{deadline}:00+09:00") if deadline else None


def is_predeadline(document: dict, cutoff: datetime | None) -> bool:
    if not cutoff or not document.get("fetched_at"):
        return False
    return datetime.fromisoformat(document["fetched_at"]) <= cutoff


def expected_1103_tickets() -> dict[int, list[str]]:
    path = FIXTURES / "kiryu_20261103_reproduced_probabilities.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {int(row["race"]): row["normal_tickets"].split(";") for row in csv.DictReader(handle)}


def model_races_1103(venue: dict) -> dict[int, dict]:
    """Rebuild the exact frozen Nov-03 replay input used by the existing test."""
    with (FIXTURES / "kiryu_20261103_exhibition_72boats.csv").open(encoding="utf-8-sig", newline="") as handle:
        boat_rows = list(csv.DictReader(handle))
    with (FIXTURES / "kiryu_20261103_boaters_escape_vs_results.csv").open(encoding="utf-8-sig", newline="") as handle:
        escape = {int(row["race"]): row for row in csv.DictReader(handle)}
    venue_races = {int(row["race"]): row for row in venue["races"]}
    output = {}
    for race_no in range(1, 13):
        official = {int(row["lane"]): row for row in venue_races[race_no]["racers"]}
        racers = []
        selected = sorted((row for row in boat_rows if int(float(row["race"])) == race_no), key=lambda row: int(float(row["lane"])))
        for row in selected:
            lane = int(float(row["lane"]))
            racers.append({
                "lane": lane, "entry_course": int(float(row["exhibition_course"])),
                "player_id": str(int(float(row["pre_reg_no"]))), "motor_no": str(int(float(row["pre_motor_no"]))),
                "boat_no": str(int(float(row["pre_boat_no"]))), "nat_win": float(row["national_win_rate"]),
                "local_win": float(row["local_win_rate"]), "nat_2": float(row["national_2ren_rate"]),
                "local_2": float(row["local_2ren_rate"]), "nat_3": 0, "local_3": 0,
                "motor_2": float(row["motor_2ren_rate"]), "motor_3": float(row["motor_3ren_rate"]),
                "avg_st": official[lane].get("avg_st"), "season_groups": [],
                "boaters_kimarite_starts": float(escape[race_no]["boaters_lane1_starts"]) if lane == 1 else None,
                "boaters_escape_rate": float(escape[race_no]["boaters_escape_pct"]) if lane == 1 else None,
            })
        output[race_no] = {"race": race_no, "eventDay": 1, "racers": racers}
    return output


def corrected_joint(base: np.ndarray, direct: dict, original: list[dict], entry_changed: bool) -> np.ndarray:
    speed = float(direct["wind_speed"])
    if speed >= 6 or entry_changed:
        return base.copy()
    straight = lower_is_better([parse_time(row.get("straight_time")) for row in original])
    turn = lower_is_better([parse_time(row.get("turn_time")) for row in original])
    direction = int(direct["wind_direction"])
    angle = 2.0 * math.pi * (min(max(direction, 1), 16) - 1.0) / 16.0
    straight_weight = (1.0 + math.cos(angle)) / 2.0
    composite = min(speed, 5.0) / 2.0 * (straight_weight * straight + (1.0 - straight_weight) * turn)
    scores = tuple(CORRECTION_COEFFICIENT * composite for _ in range(3))
    return apply_scores(base, scores)


def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    engine = KiryuV1()
    expected3 = expected_1103_tickets()
    races_rows: list[dict] = []
    boats_rows: list[dict] = []
    probability_rows: list[dict] = []
    ticket_rows: list[dict] = []
    source_rows: list[dict] = []
    race_sums: dict[str, tuple[float, float | None]] = {}
    ticket_matches = 0
    ticket_mismatches: list[dict] = []
    reference_hits = 0
    eligible_hits = 0
    eligible_count = 0
    entry_changes: list[str] = []
    missing_counts = 0

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
            pre_flags = {name: is_predeadline(docs[name], cutoff) for name in ("direct", "exhibition", "original_exhibition")}
            eligible = all(pre_flags.values())
            audit_reason = "prediction inputs captured before deadline" if eligible else "prediction inputs not proven before deadline"
            if date == "2026-11-04" and race_no == 1 and not eligible:
                audit_reason = "excluded: prior audit carried forward; original exhibition first captured after deadline"

            direct = docs["direct"]["data"]
            exhibit = sorted(docs["exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            original = sorted(docs["original_exhibition"]["data"]["entries"], key=lambda row: int(row["lane"]))
            result = docs["result"]["data"]
            actual_entry = direct.get("actual_entry") or list(range(1, 7))
            entry_changed = actual_entry != list(range(1, 7))
            if entry_changed:
                entry_changes.append(race_id)

            # Morning baseline uses frame order; later venue updates must not leak exhibition entry.
            model_race = deepcopy(replay3[race_no]) if replay3 is not None else deepcopy(race)
            if replay3 is None:
                for racer in model_race.get("racers") or []:
                    racer["entry_course"] = int(racer["lane"])
                    racer["actual_course"] = int(racer["lane"])
            base = baseline_joint(engine, model_race, date)
            base_indices = select_622(engine, base)
            base_tickets = [combo(index) for index in base_indices]
            if date == "2026-11-03":
                expected = expected3[race_no]
            else:
                expected = [
                    (row.get("combo") or row.get("combination")) if isinstance(row, dict) else str(row)
                    for row in race["predictionPre"]["tickets"]
                ]
            ticket_match = base_tickets == expected
            ticket_matches += int(ticket_match)
            if not ticket_match:
                ticket_mismatches.append({"race_id": race_id, "expected": expected, "actual": base_tickets})
            truth = "-".join(str(value) for value in result.get("order", [])[:3])
            reference_eligible = not (date == "2026-11-04" and race_no == 1)
            hit = truth in base_tickets
            reference_hits += int(reference_eligible and hit)
            eligible_count += int(eligible)
            eligible_hits += int(eligible and hit)

            corrected = corrected_joint(base, direct, original, entry_changed) if eligible else None
            race_sums[race_id] = (float(base.sum()), float(corrected.sum()) if corrected is not None else None)
            for index, perm in enumerate(PERMS):
                probability_rows.append({
                    "race_id": race_id,
                    "date": date,
                    "race": race_no,
                    "combination": "-".join(str(int(value) + 1) for value in perm),
                    "probability_before": f"{float(base[index]):.17g}",
                    "probability_after_research_candidate": "" if corrected is None else f"{float(corrected[index]):.17g}",
                    "correction_coefficient": "" if corrected is None else f"{CORRECTION_COEFFICIENT:.3f}",
                    "correction_status": "not_adopted_research_candidate" if corrected is not None else "not_applied_audit_ineligible",
                    "correction_condition": "wind<6m; no entry change; same score at all 3 conditional positions" if corrected is not None else "",
                })

            for rank, (index, combination) in enumerate(zip(base_indices, base_tickets), 1):
                ticket_rows.append({
                    "race_id": race_id, "date": date, "race": race_no, "ticket_rank": rank,
                    "allocation_segment": "C_top6" if rank <= 6 else ("D_power_0.15_next2" if rank <= 8 else "scenario_next2"),
                    "combination": combination, "probability_before": f"{float(base[index]):.17g}",
                    "matches_stored_C_plus_D": ticket_match,
                })

            racers = sorted(race.get("racers") or [], key=lambda row: int(row["lane"]))
            ex_by_lane = {int(row["lane"]): row for row in exhibit}
            orig_by_lane = {int(row["lane"]): row for row in original}
            for racer in racers:
                lane = int(racer["lane"])
                ex = ex_by_lane[lane]
                orig = orig_by_lane[lane]
                missing_counts += 1
                boats_rows.append({
                    "race_id": race_id, "date": date, "race": race_no, "lane": lane,
                    "player_name": racer.get("name"), "registration_no": racer.get("player_id"), "class": racer.get("class"),
                    "national_avg_st": racer.get("avg_st"),
                    "local_avg_st": racer.get("boaters_local_avg_st") or racer.get("local_st"),
                    "local_st_sample_count": "",
                    "official_local_st_reference": racer.get("local_st"),
                    "exhibition_st": ex.get("start_time"), "exhibition_course": ex.get("exhibition_course"),
                    "exhibition_time": ex.get("exhibition_time"),
                    "half_lap_time": orig.get("half_lap_time", orig.get("lap_time")),
                    "turn_time": orig.get("turn_time"), "straight_time": orig.get("straight_time"),
                    "course_specific_avg_st": "", "course_specific_st_sample_count": "",
                    "input_predeadline_proven": eligible,
                })

            races_rows.append({
                "race_id": race_id, "date": date, "race": race_no, "deadline_jst": f"{date}T{deadline}:00+09:00",
                "wind_direction_code": direct.get("wind_direction"), "wind_speed_mps": direct.get("wind_speed"),
                "wave_height_cm": direct.get("wave_height"), "confirmed_trifecta": truth,
                "actual_entry": "-".join(str(value) for value in actual_entry), "entry_changed": entry_changed,
                "predeadline_inputs_proven": eligible, "validation_eligible": eligible, "audit_reason": audit_reason,
                "baseline_ticket_hit": hit, "baseline_ticket_match": ticket_match,
                "correction_adoption": "none", "research_candidate_coefficient": f"{CORRECTION_COEFFICIENT:.3f}" if eligible else "",
            })

            missing = []
            if any(row.get("half_lap_time", row.get("lap_time")) is None for row in original):
                missing.append("half_lap_time")
            missing.extend(["local_st_sample_count", "course_specific_avg_st", "course_specific_st_sample_count"])
            for name, doc in docs.items():
                source_rows.append({
                    "race_id": race_id, "date": date, "race": race_no, "source_type": name,
                    "source_url": doc.get("source"), "selected_from": doc.get("_selected_from"),
                    "fetched_at": doc.get("fetched_at"), "deadline_jst": f"{date}T{deadline}:00+09:00",
                    "captured_predeadline": is_predeadline(doc, cutoff) if name != "result" else "not_applicable_result",
                    "missing_fields": ";".join(sorted(set(missing))) if name in ("direct", "exhibition", "original_exhibition") else "",
                    "used_for_prediction_features": name in ("direct", "exhibition", "original_exhibition") and eligible,
                })
            source_rows.append({
                "race_id": race_id, "date": date, "race": race_no, "source_type": "morning_venue_payload",
                "source_url": "repository:data/venues/kiryu/" + date.replace("-", "") + ".json",
                "selected_from": "local repository", "fetched_at": "", "deadline_jst": f"{date}T{deadline}:00+09:00",
                "captured_predeadline": "repository provenance; exact fetch timestamp unavailable",
                "missing_fields": "local_st_sample_count;course_specific_avg_st;course_specific_st_sample_count",
                "used_for_prediction_features": True,
            })

    assert len(races_rows) == 36
    assert len(boats_rows) == 216
    assert len(probability_rows) == 4320
    assert len(ticket_rows) == 360
    assert ticket_matches == 36, json.dumps(ticket_mismatches, ensure_ascii=False)
    assert reference_hits == 18
    assert eligible_count == 23
    max_before_error = max(abs(value[0] - 1.0) for value in race_sums.values())
    max_after_error = max(abs(value[1] - 1.0) for value in race_sums.values() if value[1] is not None)
    assert max_before_error < 1e-12 and max_after_error < 1e-12

    write_csv("races.csv", races_rows)
    write_csv("boats.csv", boats_rows)
    write_csv("probabilities.csv", probability_rows)
    write_csv("tickets.csv", ticket_rows)
    write_csv("sources.csv", source_rows)

    readme = f"""# Kiryu slit validation 2026-11-03..05

- Scope: 36 races / 216 boat rows / 4,320 trifecta probability rows.
- Frozen baseline: current improved C + normal D (power 0.15), formal 6+2+2 selector.
- Ticket reproduction: 36/36 races exactly match the stored/fixture C+D ten tickets.
- Probability normalization: before max error `{max_before_error:.3g}`; research-candidate after max error `{max_after_error:.3g}`.
- Prior reference reproduced: 18/35 races (2026-11-04 R1 excluded), with no difference.
- Strict pre-deadline validation set: {eligible_count}/36 races; baseline hit {eligible_hits}/{eligible_count}.
- Audit exclusions: all 12 races on 2026-11-03 lack proof that exhibition inputs were captured before deadline; 2026-11-04 R1 carries forward the prior exclusion.
- Entry changes: {', '.join(entry_changes)}. Candidate correction is neutralized for these races.
- Correction adoption: none. `probability_after_research_candidate` is a reproducible research-only scenario using coefficient 0.100, wind below 6m, no entry change, and the same wind-direction blend score at all three conditional positions.
- Composite score: `min(wind_speed,5)/2 * (((1+cos(angle))/2)*straight_z + ((1-cos(angle))/2)*turn_z)`; angle is derived from direction code 1..16. Lower straight/turn time is better. Results are used only for hit scoring.
- Missing values are blank and were not imputed. Local-ST aggregation counts and course-specific ST/counts are unavailable for all {missing_counts} boat rows. Half-lap is also absent in the preserved source for 2026-11-04 R2-R10.
- CSV files use UTF-8 with BOM. `race_id` is the join key. Probabilities are one row per ordered trifecta combination.

## Provenance

2026-11-03 documents are read from Git commit `{GIT_1103}` and were fetched after all race deadlines. 2026-11-04/05 documents are the preserved VPS snapshots. Per-document URL, fetch time, deadline comparison, missing fields, and feature-use flag are in `sources.csv`.

## Reproduction

From the `kiryu_ai_prod_20261104` worktree, run:

`python scripts/build_kiryu_slit_validation_package.py`

The script reuses `automation/kiryu_v1_engine.py` and `scripts/validate_kiryu_20261104_05_wind_exhibition.py`; it performs no network access and changes no production engine, coefficient, ticket, site, or publication file.
"""
    (OUTDIR / "README.md").write_text(readme, encoding="utf-8")

    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("races.csv", "boats.csv", "probabilities.csv", "tickets.csv", "sources.csv", "README.md"):
            archive.write(OUTDIR / name, arcname=name)

    print(json.dumps({
        "zip": str(ZIP_PATH), "races": len(races_rows), "boats": len(boats_rows),
        "probability_rows": len(probability_rows), "ticket_rows": len(ticket_rows),
        "ticket_matches": ticket_matches, "reference_hits": f"{reference_hits}/35",
        "predeadline_verified": eligible_count, "eligible_hits": f"{eligible_hits}/{eligible_count}",
        "entry_changes": entry_changes, "max_before_error": max_before_error, "max_after_error": max_after_error,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
