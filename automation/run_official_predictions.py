#!/usr/bin/env python3
"""Fill missing official-stage predictions using each venue's existing engine.

Run engines in isolated temporary data directories and merge only predictions
for races whose deadline has not passed. Never rewrite existing LIVE/history.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

COMMANDS = {
    "kiryu": ("run_kiryu_v1.py", "--stage", "morning"),
    "toda": ("apply_toda_v5.py",),
    "heiwajima": ("apply_heiwajima_v1.py",),
    "biwako": ("run_biwako_v1_2.py", "--stage", "preliminary"),
    "fukuoka": ("run_fukuoka_v1.py", "--stage", "preliminary"),
    "omura": ("apply_omura_v1.py",),
}


def missing_races(payload: dict, now: datetime) -> set[int]:
    existing = payload.get("preds") or {}
    result = set()
    for race in payload.get("races") or []:
        number = int(race.get("race") or 0)
        deadline = str(race.get("deadline") or "")
        if not number or not deadline or existing.get(str(number)) or race.get("prediction"):
            continue
        try:
            cutoff = datetime.strptime(f"{payload['date']} {deadline}", "%Y-%m-%d %H:%M")
            cutoff = cutoff.replace(tzinfo=ZoneInfo("Asia/Tokyo"))
        except (ValueError, KeyError):
            continue
        if now < cutoff:
            result.add(number)
    return result


def merge_missing(original: dict, generated: dict, eligible: set[int]) -> tuple[dict, int]:
    merged = copy.deepcopy(original)
    originals = {int(row.get("race") or 0): row for row in merged.get("races") or []}
    created = 0
    for number in sorted(eligible):
        race = originals.get(number)
        source = next((row for row in generated.get("races") or [] if int(row.get("race") or 0) == number), {})
        new_prediction = (generated.get("preds") or {}).get(str(number))
        if not new_prediction and not source.get("prediction"):
            continue
        if new_prediction:
            merged.setdefault("preds", {})[str(number)] = copy.deepcopy(new_prediction)
        for key in ("prediction", "predictionPre", "predictionFinal"):
            if key in source and key not in race:
                race[key] = copy.deepcopy(source[key])
        created += 1
    if created:
        complete = all(
            (merged.get("preds") or {}).get(str(row.get("race"))) or row.get("prediction")
            for row in merged.get("races") or []
        ) and len(merged.get("races") or []) == 12
        merged["predictionStatus"] = "ready" if complete else "partial"
        merged["predictionReason"] = "" if merged["predictionStatus"] == "ready" else "pending_predictions"
        if generated.get("engine"):
            merged["engine"] = generated["engine"]
        if generated.get("engineVersion"):
            merged["engineVersion"] = generated["engineVersion"]
    return merged, created


def run(date: str, data_root: Path, now: datetime, venues: list[str]) -> dict:
    from official_morning import atomic_json
    from publish_official_stages import update_index_files
    from build_site_data import prediction_payload_gate

    published = {}
    results = {"date": date, "venues": {}, "generated": 0}
    compact = date.replace("-", "")
    for slug in venues:
        path = data_root / "venues" / slug / f"{compact}.json"
        if not path.is_file():
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        if original.get("date") != date:
            continue
        eligible = missing_races(original, now)
        if not eligible:
            results["venues"][slug] = "no_eligible_races"
            continue
        with tempfile.TemporaryDirectory(prefix=f"official-pred-{slug}-") as directory:
            isolated = Path(directory)
            target = isolated / "venues" / slug / f"{compact}.json"
            target.parent.mkdir(parents=True)
            atomic_json(target, original)
            script, *flags = COMMANDS[slug]
            command = [sys.executable, str(Path(__file__).parent / script),
                       "--date", date, "--data-root", str(isolated), *flags]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
                generated = json.loads(target.read_text(encoding="utf-8"))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
                results["venues"][slug] = f"engine_failed:{type(exc).__name__}"
                continue
        valid, reason = prediction_payload_gate(generated, date)
        if not valid:
            results["venues"][slug] = f"prediction_invalid:{reason}"
            continue
        # An engine may have a broader output contract. Only copy missing predictions.
        merged, count = merge_missing(original, generated, eligible)
        if count:
            atomic_json(path, merged)
            latest = path.parent / "latest.json"
            if latest.is_file():
                latest_payload = json.loads(latest.read_text(encoding="utf-8"))
                if latest_payload.get("date") == date:
                    updated_latest, _ = merge_missing(latest_payload, generated, eligible)
                    atomic_json(latest, updated_latest)
            published[slug] = merged
        results["venues"][slug] = count
        results["generated"] += count
    if published:
        update_index_files(data_root, date, published)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--venues", nargs="*", choices=sorted(COMMANDS), default=sorted(COMMANDS))
    args = parser.parse_args()
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    if args.date != now.date().isoformat():
        raise SystemExit("Only JST today is eligible")
    print(json.dumps(run(args.date, args.data_root, now, args.venues), ensure_ascii=False))


if __name__ == "__main__":
    main()
