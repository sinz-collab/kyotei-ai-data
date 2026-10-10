from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

try:
    from .lane1_fly import attach, predict_morning, predict_post_exhibition
    from .tokoname_site_pipeline import atomic_write_json, load_json, load_live_documents
except ImportError:
    from lane1_fly import attach, predict_morning, predict_post_exhibition
    from tokoname_site_pipeline import atomic_write_json, load_json, load_live_documents


ENGINE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = ENGINE_DIR / "models"


def backfill_document(
    document: dict,
    live_root: Path,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> tuple[dict, list[dict]]:
    updated = deepcopy(document)
    reports = []
    for race in updated.get("races") or []:
        race_no = int(race["race"])
        prediction = race.get("prediction")
        if not isinstance(prediction, dict):
            reports.append({"race": race_no, "status": "skipped", "reason": "prediction_missing"})
            continue
        morning = predict_morning(updated, race, model_dir)
        attach(prediction, morning)
        try:
            documents = load_live_documents(live_root / f"{race_no:02d}", updated["date"], race_no)
            final = predict_post_exhibition(
                updated,
                race,
                documents["direct"],
                documents["exhibition"],
                model_dir,
            )
        except Exception as exc:
            prediction["lane1FlyFallback"] = {
                "status": "morning_preserved",
                "reason": f"{type(exc).__name__}: {exc}",
            }
            reports.append(
                {
                    "race": race_no,
                    "status": "morning",
                    "probability": prediction["lane1FlyProbability"],
                    "reason": prediction["lane1FlyFallback"]["reason"],
                }
            )
            continue
        attach(prediction, final)
        prediction.pop("lane1FlyFallback", None)
        reports.append(
            {
                "race": race_no,
                "status": "post_exhibition",
                "probability": prediction["lane1FlyProbability"],
            }
        )
    return updated, reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dates", nargs="+")
    parser.add_argument("--repo-root", type=Path, default=ENGINE_DIR.parents[1])
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    all_reports = {}
    for target_date in args.dates:
        compact = target_date.replace("-", "")
        venue_path = args.repo_root / "data" / "venues" / "tokoname" / f"{compact}.json"
        document = load_json(venue_path)
        updated, reports = backfill_document(
            document,
            args.repo_root / "data" / "live" / target_date / "tokoname",
        )
        if args.write:
            atomic_write_json(venue_path, updated)
            latest_path = venue_path.parent / "latest.json"
            latest = load_json(latest_path) if latest_path.is_file() else None
            if isinstance(latest, dict) and latest.get("date") == target_date:
                atomic_write_json(latest_path, updated)
        all_reports[target_date] = reports
    print(json.dumps(all_reports, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
