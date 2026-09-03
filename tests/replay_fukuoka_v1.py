from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import run_fukuoka_v1 as runner  # noqa: E402


def replay(day: str) -> dict:
    dated = ROOT / "data" / "venues" / "fukuoka" / f"{day.replace('-', '')}.json"
    live_root = ROOT / "data" / "live" / day / "fukuoka"
    payload = json.loads(dated.read_text(encoding="utf-8"))
    payload = runner.apply_predictions(payload, day, "final", live_root)
    rows = []
    for race in payload["races"]:
        race_no = int(race["race"])
        result = json.loads(
            (live_root / f"{race_no:02d}" / "result.json").read_text(encoding="utf-8")
        )
        actual = "-".join(map(str, ((result.get("data") or {}).get("order") or [])[:3]))
        tickets = {row["combo"] for row in race["prediction"]["tickets"]}
        rows.append({"race": race_no, "result": actual, "hit": actual in tickets})
    return {"date": day, "hits": sum(row["hit"] for row in rows), "races": rows}


if __name__ == "__main__":
    reports = [replay(day) for day in ("2026-09-02", "2026-09-03")]
    print(json.dumps({"reports": reports, "totalHits": sum(row["hits"] for row in reports)}, ensure_ascii=False, indent=2))
