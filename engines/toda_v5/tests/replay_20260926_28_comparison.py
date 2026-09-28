"""Replay Toda 2026-09-26 through 2026-09-28 without result leakage.

Prediction subprocesses emit only forecasts. The parent reads official results
after both the current and comparison engines have completed all 31 forecasts.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[3]
RACE_COUNTS = {"2026-09-26": 12, "2026-09-27": 12, "2026-09-28": 7}


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _context(payload, race):
    weather = race.get("weather") or (race.get("live") or {}).get("weather") or {}
    tide = race.get("tide") or payload.get("tide") or {}
    return {
        "wind_speed": weather.get("wind_speed", weather.get("wind")),
        "wind_direction": weather.get("wind_direction", weather.get("windDirection")),
        "wave_height": weather.get("wave_height", weather.get("wave")),
        "tide_phase": race.get("tide_phase") or tide.get("phase") or tide.get("label"),
        "tide_type": tide.get("tideType") or tide.get("tide_type") or tide.get("type"),
        "event_day": race.get("eventDay") or payload.get("eventDay"),
    }


def _emit_predictions(engine_dir):
    sys.path.insert(0, str(Path(engine_dir).resolve()))
    from toda_live_review_v5 import apply_live_review
    from toda_prediction_engine_v5 import TodaPredictionEngineV5

    output = []
    for date, race_count in RACE_COUNTS.items():
        payload = _load(REPO_ROOT / "data" / "venues" / "toda" / (date.replace("-", "") + ".json"))
        races = {int(race["race"]): race for race in payload["races"]}
        for race_no in range(1, race_count + 1):
            race = races[race_no]
            prediction = TodaPredictionEngineV5().predict(race, _context(payload, race))
            live_root = REPO_ROOT / "data" / "live" / date / "toda" / f"{race_no:02d}"
            documents = {
                name: _load(live_root / f"{name}.json")
                for name in ("direct", "exhibition", "original_exhibition")
            }
            if not apply_live_review(prediction, documents):
                raise RuntimeError(f"live review failed for {date} {race_no}R")
            top1 = max(prediction["win"], key=prediction["win"].get)
            output.append({
                "date": date,
                "race": race_no,
                "top1": int(top1),
                "top1P1": prediction["win"][top1],
                "tickets": prediction["ai"],
                "upset": prediction.get("aiUpset") or [],
                "scenarios": prediction.get("scenarios") or [],
            })
    print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))


def _forecast(script, engine_dir):
    child_env = os.environ.copy()
    child_env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(script), "--emit-predictions", "--engine-dir", str(engine_dir)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env,
    )
    return json.loads(completed.stdout)


def _compare(old_engine_dir):
    script = Path(__file__).resolve()
    new_rows = _forecast(script, script.parents[1])
    old_rows = _forecast(script, old_engine_dir)
    old_by_key = {(row["date"], row["race"]): row for row in old_rows}
    rows = []
    for new in new_rows:
        date = new["date"]
        race_no = new["race"]
        old = old_by_key[(date, race_no)]
        result_doc = _load(REPO_ROOT / "data" / "live" / date / "toda" / f"{race_no:02d}" / "result.json")
        order = [int(value) for value in result_doc["data"]["order"][:3]]
        result = "-".join(str(value) for value in order)
        new_hit = next((ticket for ticket in new["tickets"] if ticket["combo"] == result), None)
        old_normal_hit = any(ticket["combo"] == result for ticket in old["tickets"])
        old_upset_hit = any(ticket["combo"] == result for ticket in old["upset"])
        holes = [ticket for ticket in new["tickets"] if ticket["role"] == "シナリオ穴"]
        rows.append({
            "date": date,
            "race": race_no,
            "top1": new["top1"],
            "top1P1": new["top1P1"],
            "winner": order[0],
            "tickets": [ticket["combo"] for ticket in new["tickets"]],
            "result": result,
            "hit": bool(new_hit),
            "role": new_hit["role"] if new_hit else "-",
            "scenarioHoles": [ticket["combo"] for ticket in holes],
            "scenarioIds": [ticket.get("scenarioIds") or [] for ticket in holes],
            "oldNormalHit": old_normal_hit,
            "oldNormalPlusUpsetHit": old_normal_hit or old_upset_hit,
        })

    def count(field):
        return sum(bool(row[field]) for row in rows)

    top_correct = [row for row in rows if row["top1"] == row["winner"]]
    summary = {
        "races": len(rows),
        "newHits": count("hit"),
        "newHitRate": round(count("hit") * 100 / len(rows), 1),
        "oldNormalHits": count("oldNormalHit"),
        "oldNormalPlusUpsetHits": count("oldNormalPlusUpsetHit"),
        "gainedVsOldNormal": sum(row["hit"] and not row["oldNormalHit"] for row in rows),
        "lostVsOldNormal": sum(not row["hit"] and row["oldNormalHit"] for row in rows),
        "gainedVsOldPlusUpset": sum(row["hit"] and not row["oldNormalPlusUpsetHit"] for row in rows),
        "lostVsOldPlusUpset": sum(not row["hit"] and row["oldNormalPlusUpsetHit"] for row in rows),
        "top1Correct": len(top_correct),
        "top1CorrectAndTicketHit": sum(row["hit"] for row in top_correct),
        "scenarioHoleHits": sum(row["hit"] and row["role"] == "シナリオ穴" for row in rows),
        "wrongTop1ScenarioRescues": sum(
            row["top1"] != row["winner"] and row["hit"] and row["role"] == "シナリオ穴"
            for row in rows
        ),
        "byDate": {
            date: {
                "hits": sum(row["hit"] for row in rows if row["date"] == date),
                "races": sum(row["date"] == date for row in rows),
            }
            for date in RACE_COUNTS
        },
    }
    print(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--emit-predictions", action="store_true")
    parser.add_argument("--engine-dir")
    parser.add_argument("--old-engine-dir")
    args = parser.parse_args()
    if args.emit_predictions:
        _emit_predictions(args.engine_dir)
        return
    if not args.old_engine_dir:
        parser.error("--old-engine-dir is required for comparison")
    _compare(args.old_engine_dir)


if __name__ == "__main__":
    main()
