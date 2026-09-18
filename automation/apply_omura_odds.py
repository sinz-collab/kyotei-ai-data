from __future__ import annotations

import argparse
import json
from pathlib import Path

from apply_ashiya_live_v1 import atomic_write_json, complete_data, load_json


def apply_odds(payload: dict, target_date: str, race_no: int, race_dir: Path) -> bool:
    if payload.get("date") != target_date:
        return False
    document = load_json(race_dir / "odds.json")
    if not document or (
        document.get("date") != target_date
        or document.get("venue") != "omura"
        or int(document.get("race_no") or 0) != race_no
    ):
        return False
    data = complete_data(race_dir / "odds.json")
    odds = (data or {}).get("odds")
    if not isinstance(odds, dict) or len(odds) != 120:
        return False
    race = next(
        (row for row in payload.get("races") or [] if int(row.get("race") or 0) == race_no),
        None,
    )
    if race is None:
        return False

    changed = race.get("odds") != odds
    race["odds"] = dict(odds)
    predictions = [race.get(key) for key in ("predictionFinal", "prediction")]
    predictions.append((payload.get("preds") or {}).get(str(race_no)))
    for prediction in predictions:
        if not isinstance(prediction, dict):
            continue
        changed = changed or prediction.get("odds") != odds
        prediction["odds"] = dict(odds)
        for key in ("tickets", "ai", "aiUpset", "balance"):
            for ticket in prediction.get(key) or []:
                if not isinstance(ticket, dict):
                    continue
                combo = str(ticket.get("combo") or "")
                value = odds.get(combo, "-")
                changed = changed or ticket.get("odds") != value
                ticket["odds"] = value
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--race", required=True, type=int, choices=range(1, 13))
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--live-root", required=True, type=Path)
    args = parser.parse_args()
    venue_root = args.data_root / "venues" / "omura"
    paths = [venue_root / f"{args.date.replace('-', '')}.json", venue_root / "latest.json"]
    updated = []
    for path in paths:
        payload = load_json(path)
        if payload and apply_odds(payload, args.date, args.race, args.live_root):
            atomic_write_json(path, payload)
            updated.append(str(path))
    print(json.dumps({"venue": "omura", "race": args.race, "oddsUpdated": updated}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
