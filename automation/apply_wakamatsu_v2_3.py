from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import apply_wakamatsu_v2 as v22
from wakamatsu_v2_3_adjustments import (
    ENGINE_ID,
    ENGINE_VERSION,
    apply_v23_to_payload,
)


def apply_wakamatsu_v2_3(payload: dict, target_date: str) -> dict:
    working = deepcopy(payload)
    # A v2.2 prediction may already be present in archived/replay data.  Rebuild
    # both phases from the saved inputs so v2.3 never mixes engine generations.
    for race in working.get("races") or []:
        race.pop("predictionPre", None)
        race.pop("predictionFinal", None)
        race.pop("prediction", None)

    previous_id, previous_version = v22.ENGINE_ID, v22.ENGINE_VERSION
    try:
        v22.ENGINE_ID = ENGINE_ID
        v22.ENGINE_VERSION = ENGINE_VERSION
        generated = v22.apply_wakamatsu_v2(working, target_date)
    finally:
        v22.ENGINE_ID = previous_id
        v22.ENGINE_VERSION = previous_version
    return apply_v23_to_payload(generated)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--require-open", action="store_true")
    args = parser.parse_args()

    data_root = Path(args.data_root)
    compact = args.date.replace("-", "")
    dated_path = data_root / "venues" / "wakamatsu" / f"{compact}.json"
    latest_path = data_root / "venues" / "wakamatsu" / "latest.json"
    if not dated_path.exists():
        message = f"Wakamatsu data is not open: {dated_path}"
        if args.require_open:
            raise FileNotFoundError(message)
        print(message)
        return 0

    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    payload = v22.merge_live_files(payload, args.date, data_root)
    payload = apply_wakamatsu_v2_3(payload, args.date)
    v22.atomic_write_json(dated_path, payload)
    v22.atomic_write_json(latest_path, payload)
    print(json.dumps({
        "date": args.date,
        "engine": payload["engine"],
        "engineVersion": payload["engineVersion"],
        "raceCount": len(payload["preds"]),
        "datedPath": str(dated_path),
        "latestPath": str(latest_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
