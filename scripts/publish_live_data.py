from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from live_common import ROOT, load_config, process_lock, resolve_root

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engines.tokoname_v1.tokoname_site_pipeline import validate_site_prediction


TOKONAME = "tokoname"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def copy_changed_live_files(source_root: Path, repo_root: Path) -> int:
    destination_root = repo_root / "data" / "live"
    copied = 0
    for source in source_root.rglob("*"):
        if not source.is_file() or source.is_symlink():
            continue
        destination = destination_root / source.relative_to(source_root)
        if destination.exists() and source.read_bytes() == destination.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return copied


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"json_object_required: {path}")
    return payload


def atomic_write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def race_index(document: dict) -> dict[int, dict]:
    return {
        int(race.get("race") or 0): race
        for race in document.get("races") or []
        if int(race.get("race") or 0) in range(1, 13)
    }


def without_prediction_fields(document: dict) -> dict:
    comparable = deepcopy(document)
    for key in (
        "engine",
        "engineVersion",
        "predictionStatus",
        "predictionReason",
    ):
        comparable.pop(key, None)
    for race in comparable.get("races") or []:
        race.pop("prediction", None)
    return comparable


def publish_tokoname_predictions(staging_root: Path, repo_root: Path) -> dict:
    manifest_path = repo_root / "data" / "manifest.json"
    if not manifest_path.is_file():
        return {"status": "not_ready", "reason": "manifest_missing", "paths": []}

    manifest = load_json(manifest_path)
    venue = next(
        (
            item
            for item in manifest.get("venues") or []
            if item.get("slug") == TOKONAME
        ),
        None,
    )
    if not venue or venue.get("open") is not True:
        return {
            "status": "not_ready",
            "reason": "tokoname_not_running",
            "paths": [],
        }

    compact_date = str(manifest.get("dateDir") or "")
    target_date = str(manifest.get("date") or "")
    if not compact_date or not target_date:
        return {"status": "not_ready", "reason": "manifest_date_missing", "paths": []}

    staged_path = staging_root / "venues" / TOKONAME / f"{compact_date}.json"
    if not staged_path.is_file():
        return {
            "status": "not_ready",
            "reason": "staged_prediction_missing",
            "paths": [],
        }

    staged = load_json(staged_path)
    staged_races = race_index(staged)
    if (
        staged.get("venueId") != TOKONAME
        or staged.get("date") != target_date
        or set(staged_races) != set(range(1, 13))
    ):
        return {
            "status": "not_ready",
            "reason": "staged_prediction_identity_invalid",
            "paths": [],
        }
    publishable = {}
    for race_no in range(1, 13):
        prediction = staged_races[race_no].get("prediction")
        if not isinstance(prediction, dict) or prediction.get("status") != "ready":
            continue
        try:
            validate_site_prediction(prediction)
        except (KeyError, TypeError, ValueError):
            continue
        publishable[race_no] = prediction
    if not publishable:
        return {
            "status": "not_ready",
            "reason": "no_publishable_predictions",
            "paths": [],
        }

    dated_relative = Path("data") / "venues" / TOKONAME / f"{compact_date}.json"
    latest_relative = Path("data") / "venues" / TOKONAME / "latest.json"
    dated_path = repo_root / dated_relative
    latest_path = repo_root / latest_relative
    if not dated_path.is_file():
        return {
            "status": "not_ready",
            "reason": "published_morning_json_missing",
            "paths": [],
        }

    destination = load_json(dated_path)
    destination_races = race_index(destination)
    if (
        destination.get("venueId") != TOKONAME
        or destination.get("date") != target_date
        or set(destination_races) != set(range(1, 13))
    ):
        return {
            "status": "not_ready",
            "reason": "published_morning_json_invalid",
            "paths": [],
        }

    latest = load_json(latest_path) if latest_path.is_file() else deepcopy(destination)
    if (
        latest.get("venueId") != TOKONAME
        or latest.get("date") != target_date
        or set(race_index(latest)) != set(range(1, 13))
    ):
        latest = deepcopy(destination)

    changed_races = []
    merged_documents = []
    for original in (destination, latest):
        merged = deepcopy(original)
        merged_races = race_index(merged)
        for race_no, prediction in publishable.items():
            if merged_races[race_no].get("prediction") != prediction:
                merged_races[race_no]["prediction"] = deepcopy(prediction)
                if race_no not in changed_races:
                    changed_races.append(race_no)
        merged["engine"] = "tokoname_engine"
        merged["engineVersion"] = "1.6"
        merged["predictionStatus"] = "ready"
        merged["predictionReason"] = None
        if without_prediction_fields(merged) != without_prediction_fields(original):
            raise RuntimeError("tokoname_non_prediction_fields_changed")
        merged_documents.append(merged)
    merged_dated, merged_latest = merged_documents

    updated_manifest = deepcopy(manifest)
    updated_venue = next(
        item
        for item in updated_manifest.get("venues") or []
        if item.get("slug") == TOKONAME
    )
    updated_venue["predictionAvailable"] = True
    updated_venue["prediction_available"] = True
    updated_venue["predictionStatus"] = "ready"
    updated_venue["prediction_status"] = "ready"
    updated_venue["predictionReason"] = ""
    updated_venue["prediction_reason"] = ""
    updated_venue["availabilityReason"] = "ok"

    if (
        load_json(dated_path) == merged_dated
        and latest_path.is_file()
        and load_json(latest_path) == merged_latest
        and load_json(manifest_path) == updated_manifest
    ):
        return {
            "status": "unchanged",
            "reason": "already_published",
            "date": target_date,
            "published_races": [],
            "available_races": sorted(publishable),
            "paths": [],
        }

    atomic_write_json(dated_path, merged_dated)
    atomic_write_json(latest_path, merged_latest)
    atomic_write_json(manifest_path, updated_manifest)
    return {
        "status": "published",
        "reason": "ok",
        "date": target_date,
        "published_races": sorted(changed_races),
        "available_races": sorted(publishable),
        "paths": [
            str(dated_relative),
            str(latest_relative),
            "data/manifest.json",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Optional batched Git backup for live JSON")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    config = load_config()
    live_root = resolve_root(config, "live_output_root")
    publish_repo = resolve_root(config, "publish_repo_root")
    lock_path = resolve_root(config, "publish_lock_path")
    with process_lock(lock_path) as acquired:
        if not acquired:
            print("Another live publisher is running.")
            return 0
        if not (publish_repo / ".git").is_dir():
            print(f"Publisher repository is not initialized: {publish_repo}")
            return 2
        if args.push:
            pulled = run(["git", "pull", "--rebase", "origin", "main"], publish_repo)
            if pulled.returncode:
                print(pulled.stderr)
                return pulled.returncode
        copied = copy_changed_live_files(live_root, publish_repo)
        tokoname = publish_tokoname_predictions(
            ROOT / "runtime" / "predictions",
            publish_repo,
        )
        print(f"Tokoname prediction publish: {tokoname['status']} ({tokoname['reason']})")
        run(["git", "add", "-f", "data/live"], publish_repo)
        for path in tokoname["paths"]:
            run(["git", "add", "-f", path], publish_repo)
        if run(["git", "diff", "--cached", "--quiet"], publish_repo).returncode == 0:
            print("No publishable data changes.")
            return 0
        stamp = time.strftime("%Y-%m-%d %H:%M")
        if tokoname["status"] == "published" and copied:
            message = f"Update live data and Tokoname predictions {stamp}"
        elif tokoname["status"] == "published":
            message = f"Publish Tokoname predictions {tokoname['date']}"
        else:
            message = f"Update live race data {stamp} ({copied} files)"
        commit = run(
            ["git", "commit", "-m", message],
            publish_repo,
        )
        if commit.returncode:
            print(commit.stderr)
            return commit.returncode
        if args.push:
            pushed = run(["git", "push", "origin", "HEAD:main"], publish_repo)
            if pushed.returncode:
                pulled = run(["git", "pull", "--rebase", "origin", "main"], publish_repo)
                if pulled.returncode:
                    print(pulled.stderr)
                    return pulled.returncode
                pushed = run(["git", "push", "origin", "HEAD:main"], publish_repo)
                if pushed.returncode:
                    print(pushed.stderr)
                    return pushed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
