from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import run_fukuoka_v1 as runner  # noqa: E402
from fukuoka_prediction_engine_v1_0 import FukuokaPredictionEngineV10  # noqa: E402


def replay(day: str) -> dict:
    dated = ROOT / "data" / "venues" / "fukuoka" / f"{day.replace('-', '')}.json"
    live_root = ROOT / "data" / "live" / day / "fukuoka"
    payload = json.loads(dated.read_text(encoding="utf-8"))
    payload = runner.apply_predictions(payload, day, "final", live_root)
    rows = []
    for race in payload["races"]:
        race_no = int(race["race"])
        documents = runner.live_documents(live_root / f"{race_no:02d}", day, race_no)
        race_input = runner.build_engine_input(payload, race, documents)
        debug_prediction = FukuokaPredictionEngineV10().predict(race_input, debug=True)
        result = json.loads(
            (live_root / f"{race_no:02d}" / "result.json").read_text(encoding="utf-8")
        )
        actual = "-".join(map(str, ((result.get("data") or {}).get("order") or [])[:3]))
        tickets = {row["combo"] for row in race["prediction"]["tickets"]}
        rows.append({
            "race": race_no,
            "win": {
                str(boat["lane"]): round(boat["win_prob"] * 100.0, 4)
                for boat in debug_prediction["boats"]
            },
            "winAudit": debug_prediction["diagnostics"]["win_audit"],
            "result": actual,
            "hit": actual in tickets,
        })
    return {"date": day, "hits": sum(row["hit"] for row in rows), "races": rows}


if __name__ == "__main__":
    reports = [replay(day) for day in ("2026-09-02", "2026-09-03")]
    print(json.dumps({"reports": reports, "totalHits": sum(row["hits"] for row in reports)}, ensure_ascii=False, indent=2))
