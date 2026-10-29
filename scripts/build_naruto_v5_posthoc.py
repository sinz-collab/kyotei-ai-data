from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
AUTOMATION = ROOT / "automation"
if str(AUTOMATION) not in sys.path:
    sys.path.insert(0, str(AUTOMATION))

from naruto_v5_engine import NarutoV5Engine  # noqa: E402
from run_naruto_v5 import live_entries, normalize_racers  # noqa: E402


PREDICTION_INPUTS = ("direct", "exhibition", "original_exhibition")
INVENTORY_ITEMS = (*PREDICTION_INPUTS, "odds", "result")


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return path.absolute().relative_to(ROOT.absolute()).as_posix()
    except ValueError:
        return str(path.resolve())


def missing_live_fields(documents: dict[str, dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for name in PREDICTION_INPUTS:
        document = documents.get(name) or {}
        if document.get("complete") is not True:
            missing.append(f"{name}.complete")

    direct = (documents.get("direct") or {}).get("data") or {}
    if direct.get("wind_speed") is None:
        missing.append("direct.wind_speed")
    if direct.get("wave_height") is None:
        missing.append("direct.wave_height")
    if len(direct.get("racers") or []) != 6:
        missing.append("direct.racers")
    if direct.get("entry_changed") is True:
        missing.append("direct.entry_changed_unverified")
    if direct.get("actual_entry") != [1, 2, 3, 4, 5, 6]:
        missing.append("direct.actual_entry_unverified")

    for name, fields in (
        ("exhibition", ("exhibition_course", "start_time", "exhibition_time")),
        ("original_exhibition", ("lap_time", "turn_time", "straight_time")),
    ):
        entries = ((documents.get(name) or {}).get("data") or {}).get("entries") or []
        by_lane = {int(row.get("lane") or 0): row for row in entries}
        for lane in range(1, 7):
            if lane not in by_lane:
                missing.append(f"{name}.lane_{lane}")
                continue
            for field in fields:
                if by_lane[lane].get(field) is None:
                    missing.append(f"{name}.lane_{lane}.{field}")
    return sorted(set(missing))


def source_record(race_no: int, item: str, path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "race": race_no,
            "item": item,
            "prediction_input": item in PREDICTION_INPUTS,
            "available": False,
            "complete": False,
            "status": "missing",
            "source": "",
            "fetched_at": "",
            "missing": "file",
            "sha256": "",
        }
    document = read_json(path)
    return {
        "race": race_no,
        "item": item,
        "prediction_input": item in PREDICTION_INPUTS,
        "available": True,
        "complete": document.get("complete") is True,
        "status": document.get("status") or "",
        "source": document.get("source") or "",
        "fetched_at": document.get("fetched_at") or "",
        "missing": "" if document.get("complete") is True else (document.get("error") or "incomplete"),
        "sha256": sha256(path),
    }


def payout_yen(result: dict[str, Any]) -> int:
    digits = re.sub(r"\D", "", str((result.get("data") or {}).get("payout3t") or ""))
    if not digits:
        raise ValueError("result payout missing")
    return int(digits)


def combinations(tickets: list[dict[str, Any]]) -> list[str]:
    return [str(ticket["combo"]) for ticket in tickets]


def percent(value: float) -> float:
    return round(float(value) * 100, 6)


def metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    evaluated = [row for row in rows if row.get("verification_status") == "complete"]
    hits = sum(bool(row[f"{key}_hit"]) for row in evaluated)
    stake = sum(int(row[f"{key}_stake_yen"]) for row in evaluated)
    returned = sum(int(row[f"{key}_return_yen"]) for row in evaluated)
    return {
        "races": len(evaluated),
        "hits": hits,
        "hit_rate_pct": round(100 * hits / len(evaluated), 2) if evaluated else None,
        "stake_yen": stake,
        "return_yen": returned,
        "return_rate_pct": round(100 * returned / stake, 2) if stake else None,
    }


def fly_race_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    fly_rows = [
        row
        for row in rows
        if row.get("verification_status") == "complete" and row.get("actual_lane1_fly") is True
    ]
    hits = sum(bool(row[f"{key}_hit"]) for row in fly_rows)
    return {
        "races": len(fly_rows),
        "race_numbers": [row["race"] for row in fly_rows],
        "hits": hits,
        "hit_rate_pct": round(100 * hits / len(fly_rows), 2) if fly_rows else None,
    }


def build_report(
    *,
    payload_path: Path,
    sources_root: Path,
    artifact_path: Path,
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = read_json(payload_path)
    races = {int(row["race"]): row for row in payload.get("races") or []}
    engine = NarutoV5Engine(artifact_path)
    rows: list[dict[str, Any]] = []

    # Phase 1: only the three LIVE input documents are opened here. Result, payout,
    # odds and kimarite are deliberately unavailable to the prediction call.
    for race_no in range(1, 13):
        source_dir = sources_root / f"{race_no:02d}"
        input_documents = {
            name: read_json(source_dir / f"{name}.json")
            for name in PREDICTION_INPUTS
            if (source_dir / f"{name}.json").is_file()
        }
        missing = missing_live_fields(input_documents)
        base_row: dict[str, Any] = {
            "race": race_no,
            "recalculation_status": "unavailable" if missing else "complete",
            "recalculation_reason": ";".join(missing),
            "verification_status": "pending" if not missing else "unavailable",
        }
        if missing:
            rows.append(base_row)
            continue
        race = races.get(race_no)
        if not race:
            base_row["recalculation_status"] = "unavailable"
            base_row["recalculation_reason"] = "morning race record missing"
            base_row["verification_status"] = "unavailable"
            rows.append(base_row)
            continue

        direct = input_documents["direct"]
        exhibition = input_documents["exhibition"]
        original = input_documents["original_exhibition"]
        prediction = engine.predict_live(
            date=str(payload["date"]),
            race_no=race_no,
            racers=normalize_racers(race.get("racers") or [], direct),
            live_entries=live_entries(exhibition, original),
            wind_speed_mps=direct["data"]["wind_speed"],
            wave_height_cm=direct["data"]["wave_height"],
            generated_at=generated_at,
        )
        normal = combinations(prediction.normal_tickets)
        fly = combinations(prediction.fly_tickets)
        lane_entries = {
            int(row["lane"]): row
            for row in live_entries(exhibition, original)
        }
        base_row.update(
            {
                "model_version": prediction.model_version,
                "generated_at": prediction.generated_at,
                "actual_entry": direct["data"]["actual_entry"],
                "wind_speed_mps": direct["data"]["wind_speed"],
                "wave_height_cm": direct["data"]["wave_height"],
                "first_pct": {str(lane): percent(value) for lane, value in enumerate(prediction.first, 1)},
                "second_pct": {str(lane): percent(value) for lane, value in enumerate(prediction.second, 1)},
                "third_pct": {str(lane): percent(value) for lane, value in enumerate(prediction.third, 1)},
                "lane1_fly_probability_pct": percent(prediction.fly_probability),
                "normal_tickets": normal,
                "fly_tickets": fly,
                "live_entries": {str(lane): lane_entries[lane] for lane in range(1, 7)},
                "normal_stake_yen": 1000,
                "fly_stake_yen": 1000,
                "combined_stake_yen": 2000,
            }
        )
        rows.append(base_row)

    inventory = [
        source_record(race_no, item, sources_root / f"{race_no:02d}" / f"{item}.json")
        for race_no in range(1, 13)
        for item in INVENTORY_ITEMS
    ]

    # Phase 2: results are opened only after every prediction has been finalized.
    for row in rows:
        if row["recalculation_status"] != "complete":
            continue
        result_path = sources_root / f"{row['race']:02d}" / "result.json"
        if not result_path.is_file():
            row["verification_status"] = "unavailable"
            row["verification_reason"] = "result file missing"
            continue
        result = read_json(result_path)
        data = result.get("data") or {}
        order = [str(value) for value in data.get("order") or []]
        combo = "-".join(order[:3])
        if result.get("complete") is not True or not re.fullmatch(r"[1-6]-[1-6]-[1-6]", combo):
            row["verification_status"] = "unavailable"
            row["verification_reason"] = "result incomplete"
            continue
        payout = payout_yen(result)
        normal_hit = combo in row["normal_tickets"]
        fly_hit = combo in row["fly_tickets"]
        combined_hit = normal_hit or fly_hit
        result_odds = round(payout / 100, 1)
        row.update(
            {
                "verification_status": "complete",
                "verification_reason": "",
                "result": combo,
                "payout_yen": payout,
                "result_odds": result_odds,
                "kimarite": data.get("kimarite"),
                "actual_lane1_fly": not combo.startswith("1-"),
                "normal_hit": normal_hit,
                "normal_return_yen": payout if normal_hit else 0,
                "normal_hit_odds": result_odds if normal_hit else None,
                "fly_hit": fly_hit,
                "fly_return_yen": payout if fly_hit else 0,
                "fly_hit_odds": result_odds if fly_hit else None,
                "both_hit": normal_hit and fly_hit,
                # The user specified two independent 10 x 100-yen plans. The
                # combined return counts a shared hit only once, but the stake
                # remains 2,000 yen per evaluated race.
                "combined_hit": combined_hit,
                "combined_return_yen": payout if combined_hit else 0,
                "combined_hit_odds": result_odds if combined_hit else None,
            }
        )

    successful = [row for row in rows if row["recalculation_status"] == "complete"]
    verified = [row for row in rows if row["verification_status"] == "complete"]
    report = {
        "report_type": "posthoc_verification",
        "title": "鳴門V5 2026-10-29 LIVE事後検証",
        "disclaimer": "取得可能な過去LIVE資料による事後再計算であり、当時の本予想の再現ではない。",
        "date": payload.get("date"),
        "venue": "naruto",
        "generated_at": generated_at,
        "engine": {
            "model_version": engine.artifact.get("model_version"),
            "artifact": display_path(artifact_path),
            "artifact_sha256": sha256(artifact_path),
            "alpha": engine.artifact.get("alpha"),
            "fly_allocation": list(engine.artifact.get("allocation") or [])[:4],
            "artifact_allocation": engine.artifact.get("allocation"),
            "modified": False,
        },
        "separation": {
            "prediction_inputs": list(PREDICTION_INPUTS),
            "excluded_from_prediction": ["odds", "result", "payout", "kimarite"],
            "existing_predictions_overwritten": False,
            "existing_metrics_updated": False,
            "site_published": False,
        },
        "source_summary": {
            "races_with_complete_prediction_inputs": len(successful),
            "complete_items": sum(bool(item["complete"]) for item in inventory),
            "total_items": len(inventory),
            "missing_items": [item for item in inventory if not item["complete"]],
        },
        "recalculation": {
            "successful_races": len(successful),
            "unavailable_races": [
                {"race": row["race"], "reason": row["recalculation_reason"]}
                for row in rows
                if row["recalculation_status"] != "complete"
            ],
        },
        "verification": {
            "verified_races": len(verified),
            "normal": metrics(rows, "normal"),
            "fly": metrics(rows, "fly"),
            "combined": metrics(rows, "combined"),
            "both_hit_races": [row["race"] for row in verified if row["both_hit"]],
            "lane1_fly": {
                "actual_races": [row["race"] for row in verified if row["actual_lane1_fly"]],
                "normal": fly_race_metrics(rows, "normal"),
                "fly": fly_race_metrics(rows, "fly"),
                "combined": fly_race_metrics(rows, "combined"),
            },
        },
        "races": rows,
    }
    return report, inventory


def write_csv_outputs(
    *, report: dict[str, Any], inventory: list[dict[str, Any]], report_csv: Path, inventory_csv: Path
) -> None:
    fields = [
        "race", "recalculation_status", "recalculation_reason", "verification_status",
        "model_version", "generated_at", "actual_entry", "wind_speed_mps", "wave_height_cm",
    ]
    for lane in range(1, 7):
        fields.extend(
            [
                f"lane{lane}_exhibition_course", f"lane{lane}_exhibition_st",
                f"lane{lane}_exhibition_time", f"lane{lane}_lap_time",
                f"lane{lane}_turn_time", f"lane{lane}_straight_time",
                f"lane{lane}_first_pct", f"lane{lane}_second_pct", f"lane{lane}_third_pct",
            ]
        )
    fields.extend(
        [
            "lane1_fly_probability_pct", "normal_tickets", "fly_tickets", "result", "payout_yen",
            "result_odds", "kimarite", "actual_lane1_fly", "normal_hit", "normal_hit_odds",
            "fly_hit", "fly_hit_odds", "both_hit", "combined_hit", "combined_hit_odds",
            "normal_stake_yen", "normal_return_yen", "fly_stake_yen", "fly_return_yen",
            "combined_stake_yen", "combined_return_yen",
        ]
    )
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    with report_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for source in report["races"]:
            row = dict(source)
            row["actual_entry"] = "-".join(str(value) for value in row.get("actual_entry") or [])
            row["normal_tickets"] = "|".join(row.get("normal_tickets") or [])
            row["fly_tickets"] = "|".join(row.get("fly_tickets") or [])
            for lane in range(1, 7):
                entry = (row.get("live_entries") or {}).get(str(lane), {})
                for source_name, output_name in (
                    ("exhibition_course", "exhibition_course"), ("exhibition_st", "exhibition_st"),
                    ("exhibition_time", "exhibition_time"), ("lap_time", "lap_time"),
                    ("turn_time", "turn_time"), ("straight_time", "straight_time"),
                ):
                    row[f"lane{lane}_{output_name}"] = entry.get(source_name)
                row[f"lane{lane}_first_pct"] = (row.get("first_pct") or {}).get(str(lane))
                row[f"lane{lane}_second_pct"] = (row.get("second_pct") or {}).get(str(lane))
                row[f"lane{lane}_third_pct"] = (row.get("third_pct") or {}).get(str(lane))
            writer.writerow(row)

    inventory_csv.parent.mkdir(parents=True, exist_ok=True)
    with inventory_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]))
        writer.writeheader()
        writer.writerows(inventory)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an isolated Naruto V5 posthoc verification report.")
    parser.add_argument("--payload", type=Path, default=ROOT / "data/venues/naruto/20261029.json")
    parser.add_argument(
        "--sources-root", type=Path, default=ROOT / "data/posthoc/naruto/20261029/live_sources"
    )
    parser.add_argument(
        "--artifact", type=Path, default=ROOT / "automation/models/naruto_v5_fixed_20261020.joblib"
    )
    parser.add_argument(
        "--output-root", type=Path, default=ROOT / "data/posthoc/naruto/20261029"
    )
    parser.add_argument("--generated-at")
    args = parser.parse_args()
    generated_at = args.generated_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    report, inventory = build_report(
        payload_path=args.payload,
        sources_root=args.sources_root,
        artifact_path=args.artifact,
        generated_at=generated_at,
    )
    report_json = args.output_root / "naruto_v5_posthoc_20261029.json"
    report_csv = args.output_root / "naruto_v5_posthoc_20261029.csv"
    inventory_csv = args.output_root / "live_source_inventory.csv"
    write_json(report_json, report)
    write_csv_outputs(report=report, inventory=inventory, report_csv=report_csv, inventory_csv=inventory_csv)
    print(json.dumps({"report": str(report_json), "csv": str(report_csv), "inventory": str(inventory_csv), "summary": report["verification"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
