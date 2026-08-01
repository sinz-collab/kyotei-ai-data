from __future__ import annotations

import argparse
import json
from pathlib import Path


ENGINE_ID = "karatsu_scenario_engine_v1_2"


def result_combo(race):
    result = race.get("result") or {}
    for key in ("order", "trifecta", "result"):
        value = result.get(key)
        if isinstance(value, list) and len(value) >= 3:
            return "-".join(map(str, value[:3]))
        if isinstance(value, str) and value.count("-") >= 2:
            return value
    return None


def tickets(pred):
    out = []
    source = pred.get("tickets") or pred
    for key in ("ai", "aiUpset"):
        rows = source.get(key, []) if isinstance(source, dict) else []
        for row in rows:
            if isinstance(row, dict) and row.get("combo"):
                out.append(row["combo"])
            elif isinstance(row, str):
                out.append(row)
    return out


def trifecta_rank(pred, combo):
    rows = pred.get("trifectaTop20") or []
    for idx, row in enumerate(rows, start=1):
        if row.get("combo") == combo:
            return idx
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    args = parser.parse_args()

    date = args.date.replace("-", "")
    path = Path("data/venues/karatsu") / f"{date}.json"
    data = json.loads(path.read_text(encoding="utf-8"))

    rows = []
    for race in data.get("races", []):
        combo = result_combo(race)
        pred = race.get("prediction") or {}
        ts = tickets(pred)
        rows.append(
            {
                "race": race.get("race"),
                "result": combo,
                "hit": combo in ts if combo else None,
                "resultRankTop20": trifecta_rank(pred, combo) if combo else None,
                "sab": pred.get("sab"),
                "engine": pred.get("engine"),
                "engineVersion": pred.get("engineVersion"),
                "entryChanged": pred.get("entryChanged"),
                "actualEntry": pred.get("actualEntry"),
                "tickets": ts,
                "ticketCount": len(ts),
                "top20Count": len(pred.get("trifectaTop20") or []),
                "oddsUsedForPrediction": (
                    (pred.get("diagnostics") or {}).get("oddsUsedForPrediction")
                ),
            }
        )

    finished = [row for row in rows if row["result"]]
    payload = {
        "engine": ENGINE_ID,
        "finished": len(finished),
        "hits": sum(bool(row["hit"]) for row in finished),
        "hitRate": (
            round(sum(bool(row["hit"]) for row in finished) / len(finished), 4)
            if finished
            else None
        ),
        "invalidEngineRows": [
            row["race"] for row in rows if row["engine"] not in (None, ENGINE_ID)
        ],
        "invalidTicketCountRows": [
            row["race"] for row in rows if row["ticketCount"] not in (0, 10)
        ],
        "oddsLeakRows": [
            row["race"] for row in rows
            if row["oddsUsedForPrediction"] not in (None, False)
        ],
        "rows": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
