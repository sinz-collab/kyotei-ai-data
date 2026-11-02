from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_DIR = REPO_ROOT / "engines" / "heiwajima_v1"
AUTOMATION_DIR = REPO_ROOT / "automation"
HERE = Path(__file__).resolve().parent
for path in (ENGINE_DIR, AUTOMATION_DIR, HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from apply_heiwajima_live_v1 import live_input
from apply_heiwajima_v1 import build_player_index, engine_input_for
from heiwajima_live_review import apply_live_update
from heiwajima_master_loader import MasterLoader
from heiwajima_prediction_engine import calculate
from model import fly_probability, fly_tickets, normal_tickets


class CachedLoader(MasterLoader):
    def __init__(self):
        super().__init__()
        self._query_cache = {}
        self._table_cache = {}

    def query(self, sql, params=()):
        key = (sql, tuple(params))
        if key not in self._query_cache:
            self._query_cache[key] = super().query(sql, params)
        return self._query_cache[key].copy()

    def table(self, name):
        if name not in self._table_cache:
            self._table_cache[name] = super().table(name)
        return self._table_cache[name].copy()


def load_fixture(path, date_from=None, date_to=None, expected_count=None, require_live_blobs=False):
    raw = Path(path).read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    rows = [
        row for row in rows
        if (not date_from or row["date"] >= date_from)
        and (not date_to or row["date"] <= date_to)
        and (
            not require_live_blobs
            or all(row.get(key) for key in (
                "live_direct_blob", "live_exhibition_blob", "live_original_exhibition_blob"
            ))
        )
    ]
    keys = [(row["date"], int(row["race_no"])) for row in rows]
    actual = {
        (row["date"], int(row["race_no"])): (row.get("actual") or row.get("finish_order") or "").split("-")[:3]
        for row in rows
    }
    actual = {key: "-".join(value) for key, value in actual.items()}
    if len(set(keys)) != len(keys):
        raise RuntimeError("fixture_races_must_be_unique")
    if expected_count is not None and len(keys) != expected_count:
        raise RuntimeError(f"fixture_count_mismatch: expected={expected_count} actual={len(keys)}")
    if any(len(value.split("-")) != 3 for value in actual.values()):
        raise RuntimeError("fixture_actual_trifecta_missing")
    return keys, actual, hashlib.sha256(raw).hexdigest()


def inference_input(date, race_no, player_index, prediction_point="auto"):
    source = REPO_ROOT / "data" / "venues" / "heiwajima" / f"{date.replace('-', '')}.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    race = next(row for row in payload["races"] if int(row["race"]) == race_no)

    # Only fields consumed by engine_input_for are passed. Published predictions,
    # results, odds and history are deliberately unreachable from this object.
    clean_payload = {
        "date": payload["date"],
        "tide": deepcopy(payload.get("tide") or {}),
        "weather": deepcopy(payload.get("weather") or {}),
        "eventDay": payload.get("eventDay"),
        "seriesDay": payload.get("seriesDay"),
    }
    clean_race = {
        key: deepcopy(race.get(key))
        for key in (
            "race", "deadline", "type", "eventDay", "race_meta", "tide",
            "tide_phase", "tide_window", "weather", "racers",
        )
        if key in race
    }
    pre_input, missing = engine_input_for(clean_payload, clean_race, player_index, stage="pre")
    live_root = REPO_ROOT / "data" / "live" / date / "heiwajima" / f"{race_no:02d}"
    live, live_missing = live_input(live_root)
    live.pop("odds", None)
    if prediction_point == "morning":
        final_input = pre_input
        stage = "morning"
    elif live.get("entries"):
        final_input = apply_live_update(pre_input, live)
        stage = "live"
    elif prediction_point == "live":
        raise RuntimeError(f"live_input_missing: {date} R{race_no}")
    else:
        final_input = pre_input
        stage = "morning_fallback"
    return final_input, stage, sorted(set(missing + live_missing))


def baseline_fly_tickets(records):
    return [combo for combo in normal_tickets(records, 120) if not combo.startswith("1-")][:8]


def summarise(rows, prefix):
    count = len(rows)
    fly_correct = sum(row[f"{prefix}_fly_correct"] for row in rows)
    normal_hit = sum(row[f"{prefix}_normal_hit"] for row in rows)
    fly_hit = sum(row[f"{prefix}_ticket_hit"] for row in rows)
    union_hit = sum(row[f"{prefix}_union_hit"] for row in rows)
    return {
        "races": count,
        "fly_decision_correct": fly_correct,
        "fly_decision_accuracy": round(fly_correct / count, 6),
        "normal_hits": normal_hit,
        "normal_hit_rate": round(normal_hit / count, 6),
        "fly_ticket_hits": fly_hit,
        "fly_ticket_hit_rate": round(fly_hit / count, 6),
        "union_hits": union_hit,
        "union_hit_rate": round(union_hit / count, 6),
        "overlap_total": sum(row[f"{prefix}_overlap"] for row in rows),
        "overlap_mean": round(sum(row[f"{prefix}_overlap"] for row in rows) / count, 6),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--output", default=str(HERE / "replay_77r_result.json"))
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--prediction-point", choices=("auto", "morning", "live"), default="auto")
    parser.add_argument("--require-live-blobs", action="store_true")
    parser.add_argument("--skip-missing-live", action="store_true")
    args = parser.parse_args()
    keys, actual_by_key, fixture_sha = load_fixture(
        args.fixture, args.date_from, args.date_to, args.expected_count, args.require_live_blobs
    )
    loader = CachedLoader()
    history = loader.table("historical_tide_races")
    player_index = build_player_index()
    predictions = []

    # Phase 1: inference. Actual outcomes are not passed to any input/model call.
    for date, race_no in keys:
        try:
            model_input, stage, connector_missing = inference_input(
                date, race_no, player_index, args.prediction_point
            )
        except RuntimeError as error:
            if args.skip_missing_live and str(error).startswith("live_input_missing:"):
                continue
            raise
        result = calculate(model_input, loader=loader)
        records = result["probabilities"]
        normal = normal_tickets(records)
        baseline_probability = 1.0 - next(float(row["win_prob"]) for row in records if int(row["boat_no"]) == 1)
        candidate = fly_probability(model_input, loader)
        predictions.append({
            "date": date,
            "race_no": race_no,
            "stage": stage,
            "connector_missing": connector_missing,
            "engine_missing": (result.get("data_completeness") or {}).get("missing_codes") or [],
            "normal": normal,
            "baseline_fly_probability": round(baseline_probability, 8),
            "baseline_is_fly": baseline_probability >= 0.50,
            "baseline_fly": baseline_fly_tickets(records),
            "candidate": candidate,
            "candidate_fly": fly_tickets(records, result["scenarios"], history),
        })

    # Phase 2: scoring only after all predictions have been frozen in memory.
    scored = []
    for prediction in predictions:
        key = (prediction["date"], prediction["race_no"])
        actual = actual_by_key[key]
        actual_fly = not actual.startswith("1-")
        normal_hit = int(actual in prediction["normal"])
        baseline_hit = int(actual in prediction["baseline_fly"])
        candidate_hit = int(actual in prediction["candidate_fly"])
        row = dict(prediction)
        row.update({
            "actual": actual,
            "actual_fly": actual_fly,
            "baseline_fly_correct": int(prediction["baseline_is_fly"] == actual_fly),
            "baseline_normal_hit": normal_hit,
            "baseline_ticket_hit": baseline_hit,
            "baseline_union_hit": int(normal_hit or baseline_hit),
            "baseline_overlap": len(set(prediction["normal"]) & set(prediction["baseline_fly"])),
            "candidate_fly_correct": int(prediction["candidate"]["is_fly"] == actual_fly),
            "candidate_normal_hit": normal_hit,
            "candidate_ticket_hit": candidate_hit,
            "candidate_union_hit": int(normal_hit or candidate_hit),
            "candidate_overlap": len(set(prediction["normal"]) & set(prediction["candidate_fly"])),
        })
        scored.append(row)

    report = {
        "fixture_sha256": fixture_sha,
        "input_policy": "inference_then_score; result/payout/odds/stored predictions excluded",
        "baseline_definition": "v2.7 P(lane1 loses)=1-P1; top8 non-lane1 heads by v2.7 trifecta score",
        "candidate_definition": "model.py frozen constants",
        "stage_counts": {stage: sum(row["stage"] == stage for row in scored) for stage in sorted({row["stage"] for row in scored})},
        "actual_lane1_fly": sum(row["actual_fly"] for row in scored),
        "baseline": summarise(scored, "baseline"),
        "candidate": summarise(scored, "candidate"),
        "rows": scored,
    }
    output = Path(args.output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

