#!/usr/bin/env python3
"""Replay prediction-only changes onto a newer LIVE publication without losing LIVE fields."""

import argparse
import copy
import json
import subprocess
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args], text=True)


def at_revision(revision, path):
    try:
        return json.loads(git("show", f"{revision}:{path}"))
    except subprocess.CalledProcessError:
        return None


def replay(previous, generated, current):
    """Only replay newly generated forecasts or untouched preliminary corrections."""
    previous = previous or {}
    old_preds = previous.get("preds") or {}
    new_preds = generated.get("preds") or {}
    current_preds = current.setdefault("preds", {})
    races = {str(row.get("race")): row for row in current.get("races") or []}
    new_races = {str(row.get("race")): row for row in generated.get("races") or []}
    changed = 0
    for number, new in new_preds.items():
        old = old_preds.get(number)
        existing = current_preds.get(number)
        if not new or new == old:
            continue
        if old is None:
            # The prediction was computed while this race was still open.
            if existing or races.get(number, {}).get("prediction"):
                continue
        elif existing != old or (existing or {}).get("prediction_history", {}).get("live"):
            continue
        current_preds[number] = copy.deepcopy(new)
        race = races.get(number)
        source = new_races.get(number, {})
        if race:
            for field in ("prediction", "predictionPre", "predictionFinal"):
                if field in source and field not in race:
                    race[field] = copy.deepcopy(source[field])
        changed += 1
    if changed:
        complete = len(races) == 12 and all(
            current_preds.get(number) or row.get("prediction")
            for number, row in races.items()
        )
        current["predictionStatus"] = "ready" if complete else "partial"
        current["predictionReason"] = "" if complete else "pending_predictions"
        for field in ("engine", "engineVersion"):
            if generated.get(field):
                current[field] = generated[field]
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    parent = args.candidate + "^"
    paths = git("diff", "--name-only", parent, args.candidate, "--", "data/venues").splitlines()
    modified = {}
    for path in paths:
        if not path.endswith(f"/{args.date.replace('-', '')}.json"):
            continue
        current_path = Path(path)
        if not current_path.is_file():
            continue
        current = json.loads(current_path.read_text(encoding="utf-8"))
        generated = at_revision(args.candidate, path)
        if not generated:
            continue
        count = replay(at_revision(parent, path), generated, current)
        if count:
            current_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            latest = current_path.with_name("latest.json")
            if latest.is_file():
                latest.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            modified[current.get("venueId") or current_path.parent.name] = current
    if modified:
        # Recalculate availability on the latest manifest instead of restoring stale metadata.
        from publish_official_stages import update_index_files
        update_index_files(Path("data"), args.date, modified)
    print(json.dumps({"replayed": sorted(modified)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
