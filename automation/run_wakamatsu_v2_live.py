from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ID = "wakamatsu_engine_v2.2"
ENGINE_VERSION = "2.2"
FINAL_PREDICTION_STAGE = {
    "label": "本予想",
    "badge": "本予想",
    "statusText": "直前・展示を反映して若松v2.2で再精査済み",
    "color": "green",
}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required: {path}")
    return value


def is_complete_live_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        wrapper = load_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return wrapper.get("complete") is True and wrapper.get("status") == "complete"


def complete_live_races(data_root: Path, target_date: str) -> list[int]:
    live_root = data_root / "live" / target_date / "wakamatsu"
    if not live_root.is_dir():
        return []

    races = []
    for race_dir in live_root.iterdir():
        if not race_dir.is_dir() or not race_dir.name.isdigit():
            continue
        race_no = int(race_dir.name)
        if not 1 <= race_no <= 12:
            continue
        if all(
            is_complete_live_file(race_dir / filename)
            for filename in ("direct.json", "exhibition.json")
        ):
            races.append(race_no)
    return sorted(races)


def probability_changes(pre: dict, final: dict) -> dict:
    changes = {}
    for lane in range(1, 7):
        lane_key = str(lane)
        changes[lane_key] = {}
        for finish in ("win", "second", "third"):
            before = float((pre.get(finish) or {}).get(lane_key) or 0.0)
            after = float((final.get(finish) or {}).get(lane_key) or 0.0)
            changes[lane_key][finish] = round(after - before, 2)
    return changes


def validate_published_data(
    data_root: Path,
    target_date: str,
    expected_races: list[int],
) -> dict:
    dated_path = (
        data_root
        / "venues"
        / "wakamatsu"
        / f"{target_date.replace('-', '')}.json"
    )
    payload = load_json(dated_path)
    if payload.get("engine") != ENGINE_ID:
        raise RuntimeError(f"unexpected_engine: {payload.get('engine')}")
    if payload.get("engineVersion") != ENGINE_VERSION:
        raise RuntimeError(
            f"unexpected_engine_version: {payload.get('engineVersion')}"
        )

    races_by_number = {
        int(race.get("race") or 0): race
        for race in payload.get("races") or []
        if isinstance(race, dict)
    }
    reports = []
    for race_no in expected_races:
        race = races_by_number.get(race_no)
        if race is None:
            raise RuntimeError(f"wakamatsu_race_missing: {race_no}")

        pre = race.get("predictionPre")
        final = race.get("predictionFinal")
        active = race.get("prediction")
        if not isinstance(pre, dict):
            raise RuntimeError(f"prediction_pre_missing: {race_no}")
        if not isinstance(final, dict):
            raise RuntimeError(f"prediction_final_missing: {race_no}")
        if not isinstance(active, dict):
            raise RuntimeError(f"active_prediction_missing: {race_no}")

        required = {
            "phase": "final",
            "finalPredictionStatus": "complete",
            "engine": ENGINE_ID,
            "engineVersion": ENGINE_VERSION,
        }
        for key, expected in required.items():
            if final.get(key) != expected:
                raise RuntimeError(
                    f"prediction_final_invalid: race={race_no} "
                    f"field={key} actual={final.get(key)!r} expected={expected!r}"
                )
        if active.get("phase") != "final":
            raise RuntimeError(f"active_prediction_not_final: {race_no}")
        if active != final:
            raise RuntimeError(f"active_prediction_not_prediction_final: {race_no}")
        if final.get("predictionStage") != FINAL_PREDICTION_STAGE:
            raise RuntimeError(f"prediction_stage_not_final: {race_no}")

        tickets = final.get("tickets") or []
        combinations = [ticket.get("combo") for ticket in tickets]
        if len(tickets) != 10 or len(set(combinations)) != 10:
            raise RuntimeError(f"wakamatsu_ticket_count_invalid: {race_no}")
        if (
            (final.get("diagnostics") or {}).get("oddsUsedForPrediction")
            is not False
        ):
            raise RuntimeError(f"odds_prediction_guard_failed: {race_no}")

        reports.append(
            {
                "race": race_no,
                "engine": final.get("engine"),
                "engineVersion": final.get("engineVersion"),
                "predictionPre": True,
                "predictionFinal": True,
                "activePredictionPhase": active.get("phase"),
                "finalPredictionStatus": final.get("finalPredictionStatus"),
                "predictionStage": final.get("predictionStage"),
                "win": final.get("win"),
                "second": final.get("second"),
                "third": final.get("third"),
                "sab": final.get("sab"),
                "tickets": combinations,
                "probabilityChanges": probability_changes(pre, final),
            }
        )

    return {
        "date": target_date,
        "venue": "wakamatsu",
        "engine": payload.get("engine"),
        "engineVersion": payload.get("engineVersion"),
        "completeLiveRaces": expected_races,
        "races": reports,
    }


def run_pipeline(
    target_date: str,
    data_root: Path,
    python_executable: str = sys.executable,
) -> dict:
    try:
        date.fromisoformat(target_date)
    except ValueError as exc:
        raise RuntimeError(f"invalid_date: {target_date}") from exc

    resolved_data_root = (
        data_root if data_root.is_absolute() else REPO_ROOT / data_root
    ).resolve()
    dated_path = (
        resolved_data_root
        / "venues"
        / "wakamatsu"
        / f"{target_date.replace('-', '')}.json"
    )
    if not dated_path.is_file():
        raise FileNotFoundError(dated_path)

    expected_races = complete_live_races(resolved_data_root, target_date)
    if not expected_races:
        return {
            "date": target_date,
            "venue": "wakamatsu",
            "status": "no_complete_live_races",
            "completeLiveRaces": [],
            "races": [],
        }

    commands = [
        [
            python_executable,
            str(REPO_ROOT / "automation" / "apply_wakamatsu_v2.py"),
            "--date",
            target_date,
            "--data-root",
            str(resolved_data_root),
        ],
        [
            python_executable,
            str(REPO_ROOT / "automation" / "build_site_data.py"),
            "--date",
            target_date,
            "--data-root",
            str(resolved_data_root),
            "--live-venue",
            "wakamatsu",
        ],
    ]
    for command in commands:
        subprocess.run(command, cwd=REPO_ROOT, check=True)

    return validate_published_data(
        resolved_data_root,
        target_date,
        expected_races,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    report = run_pipeline(args.date, Path(args.data_root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
