from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import run_fukuoka_v1 as runner  # noqa: E402


def clean_payload(day: str) -> dict:
    path = ROOT / "data" / "venues" / "fukuoka" / f"{day.replace('-', '')}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["preds"] = {}
    for race in payload["races"]:
        for key in (
            "prediction", "predictionPre", "predictionFinal", "predictionCurrentV1",
            "predictionCurrentV1Pre", "predictionCurrentV1Final",
        ):
            race.pop(key, None)
    return payload


def replay(day: str) -> dict:
    live_root = ROOT / "data" / "live" / day / "fukuoka"
    preliminary = runner.apply_predictions(
        deepcopy(clean_payload(day)), day, "preliminary", live_root,
    )
    final = runner.apply_predictions(preliminary, day, "final", live_root)
    rows = []
    for race in final["races"]:
        race_no = int(race["race"])
        prediction = race["predictionFinal"]
        diagnostics = prediction["diagnostics"]
        win = prediction["win"]
        top1 = int(max(win, key=win.get))
        result_document = json.loads(
            (live_root / f"{race_no:02d}" / "result.json").read_text(encoding="utf-8")
        )
        actual = int(((result_document.get("data") or {}).get("order") or [0])[0])
        rows.append({
            "race": race_no,
            "win": win,
            "top1": top1,
            "actual": actual,
            "hit": top1 == actual,
            "weaknessScore": diagnostics["weaknessScore"],
            "headScores": diagnostics["headScores"],
            "headDominance": diagnostics["headDominance"],
            "takeover": diagnostics["takeoverActivated"],
            "takeoverLane": diagnostics["takeoverLane"],
        })
    return {
        "date": day,
        "hits": sum(row["hit"] for row in rows),
        "takeovers": [row["race"] for row in rows if row["takeover"]],
        "races": rows,
    }


if __name__ == "__main__":
    reports = [replay(day) for day in ("2026-09-25", "2026-09-26", "2026-09-27")]
    print(json.dumps({
        "reports": reports,
        "totalHits": sum(report["hits"] for report in reports),
    }, ensure_ascii=False, indent=2))
