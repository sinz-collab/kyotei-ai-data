#!/usr/bin/env python3
"""Reproduce the validated Kiryu variable fly tickets without result features."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from kiryu_v1_engine import KiryuV1


RESULTS = ROOT / "tests" / "fixtures" / "kiryu_v1" / "verified_20261104_05_results.json"
OUT = ROOT / "validation" / "kiryu_variable_fly_20261104_05"


def combos(rows: list[dict]) -> list[str]:
    return [str(row.get("combo") or row.get("combination")) for row in rows]


def main() -> None:
    engine = KiryuV1()
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    rows = []
    for day in ("2026-11-04", "2026-11-05"):
        payload = json.loads(
            (ROOT / "data" / "venues" / "kiryu" / f"{day.replace('-', '')}.json").read_text(encoding="utf-8")
        )
        for race in payload["races"]:
            race_no = int(race["race"])
            if day == "2026-11-05" and race_no == 12:
                continue
            prediction = engine.predict(race, day)
            result = results[day][str(race_no)]
            fly = prediction["flyPrediction"]
            tickets = combos(fly["tickets"])
            heads = fly["headCandidates"]
            rows.append({
                "date": day,
                "race": race_no,
                "fly_probability_pct": f"{float(fly['probability']):.10f}",
                "candidate_heads": "+".join(map(str, heads)),
                "candidate_first_rates_pct": "+".join(
                    f"{float(prediction['win'][str(head)]):.10f}" for head in heads
                ),
                "allocation": "+".join(map(str, fly["allocation"])),
                "fly_tickets": ";".join(tickets),
                "result": result,
                "lane1_flew": not result.startswith("1-"),
                "hit": result in tickets,
            })

    hits = sum(row["hit"] for row in rows)
    fly_rows = [row for row in rows if row["lane1_flew"]]
    fly_hits = sum(row["hit"] for row in fly_rows)
    race4 = next(row for row in rows if row["date"] == "2026-11-05" and row["race"] == 4)
    if (len(rows), hits, len(fly_rows), fly_hits) != (23, 8, 14, 8):
        raise AssertionError((len(rows), hits, len(fly_rows), fly_hits))
    if "2-6-1" not in race4["fly_tickets"].split(";"):
        raise AssertionError("2026-11-05 4R did not reproduce 2-6-1")

    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "races.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "scope": "2026-11-04 R1-R12 and 2026-11-05 R1-R11",
        "races": len(rows),
        "hits": hits,
        "lane1_fly_results": len(fly_rows),
        "lane1_fly_hits": fly_hits,
        "november_5_r4": {
            "result": race4["result"],
            "allocation": race4["allocation"],
            "tickets": race4["fly_tickets"].split(";"),
        },
        "selection_uses_results": False,
        "normal_probability_or_ticket_selection_changed": False,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
