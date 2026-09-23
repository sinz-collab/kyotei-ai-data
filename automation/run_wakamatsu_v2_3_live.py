from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from wakamatsu_v2_3_adjustments import ENGINE_ID, ENGINE_VERSION


REPO_ROOT = Path(__file__).resolve().parents[1]
FINAL_PREDICTION_STAGE = {
    "label": "本予想",
    "badge": "本予想",
    "statusText": "直前・展示・オリ展示を反映して若松v2.3で再精査済み",
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
        if 1 <= race_no <= 12 and all(
            is_complete_live_file(race_dir / filename)
            for filename in ("direct.json", "exhibition.json", "original_exhibition.json")
        ):
            races.append(race_no)
    return sorted(races)


def probability_changes(pre: dict, final: dict) -> dict:
    return {
        str(lane): {
            finish: round(
                float((final.get(finish) or {}).get(str(lane)) or 0.0)
                - float((pre.get(finish) or {}).get(str(lane)) or 0.0),
                2,
            )
            for finish in ("win", "second", "third")
        }
        for lane in range(1, 7)
    }


def validate_published_data(data_root: Path, target_date: str, expected_races: list[int]) -> dict:
    dated_path = data_root / "venues" / "wakamatsu" / f"{target_date.replace('-', '')}.json"
    payload = load_json(dated_path)
    if payload.get("engine") != ENGINE_ID or payload.get("engineVersion") != ENGINE_VERSION:
        raise RuntimeError("unexpected_wakamatsu_v23_engine")
    races_by_number = {int(race.get("race") or 0): race for race in payload.get("races") or []}
    reports = []
    for race_no in expected_races:
        race = races_by_number.get(race_no) or {}
        pre, final, active = race.get("predictionPre"), race.get("predictionFinal"), race.get("prediction")
        if not all(isinstance(value, dict) for value in (pre, final, active)):
            raise RuntimeError(f"wakamatsu_prediction_missing: {race_no}")
        if active != final or final.get("phase") != "final":
            raise RuntimeError(f"active_prediction_not_final: {race_no}")
        if final.get("engine") != ENGINE_ID or final.get("engineVersion") != ENGINE_VERSION:
            raise RuntimeError(f"prediction_final_engine_invalid: {race_no}")
        if final.get("predictionStage") != FINAL_PREDICTION_STAGE:
            raise RuntimeError(f"prediction_stage_not_final: {race_no}")
        for finish in ("win", "second", "third"):
            total = sum(float(value) for value in (final.get(finish) or {}).values())
            if abs(total - 100.0) > 0.02:
                raise RuntimeError(f"probability_sum_invalid: race={race_no} finish={finish} total={total}")
        tickets = final.get("tickets") or []
        combos = [ticket.get("combo") for ticket in tickets]
        if len(tickets) != 10 or len(set(combos)) != 10:
            raise RuntimeError(f"wakamatsu_ticket_count_invalid: {race_no}")
        if (final.get("diagnostics") or {}).get("oddsUsedForPrediction") is not False:
            raise RuntimeError(f"odds_prediction_guard_failed: {race_no}")
        reports.append({
            "race": race_no,
            "engine": final.get("engine"),
            "win": final.get("win"),
            "second": final.get("second"),
            "third": final.get("third"),
            "sab": final.get("sab"),
            "tickets": combos,
            "probabilityChanges": probability_changes(pre, final),
        })
    return {
        "date": target_date,
        "venue": "wakamatsu",
        "engine": payload.get("engine"),
        "engineVersion": payload.get("engineVersion"),
        "completeLiveRaces": expected_races,
        "races": reports,
    }


def run_pipeline(target_date: str, data_root: Path, python_executable: str = sys.executable) -> dict:
    try:
        date.fromisoformat(target_date)
    except ValueError as exc:
        raise RuntimeError(f"invalid_date: {target_date}") from exc
    resolved = (data_root if data_root.is_absolute() else REPO_ROOT / data_root).resolve()
    dated_path = resolved / "venues" / "wakamatsu" / f"{target_date.replace('-', '')}.json"
    if not dated_path.is_file():
        raise FileNotFoundError(dated_path)
    races = complete_live_races(resolved, target_date)
    if not races:
        return {"date": target_date, "venue": "wakamatsu", "status": "no_complete_live_races", "completeLiveRaces": [], "races": []}
    for command in (
        [python_executable, str(REPO_ROOT / "automation" / "apply_wakamatsu_v2_3.py"), "--date", target_date, "--data-root", str(resolved)],
        [python_executable, str(REPO_ROOT / "automation" / "build_site_data.py"), "--date", target_date, "--data-root", str(resolved), "--live-venue", "wakamatsu"],
    ):
        subprocess.run(command, cwd=REPO_ROOT, check=True)
    return validate_published_data(resolved, target_date, races)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()
    print(json.dumps(run_pipeline(args.date, Path(args.data_root)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
