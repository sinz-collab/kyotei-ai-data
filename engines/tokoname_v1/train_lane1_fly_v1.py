from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline


MODEL_VERSION = "tokoname_lane1_fly_v1"
VALIDATION_START = "2026-09-23"
LANES = tuple(range(1, 7))
GRADE = {"B2": 0.0, "B1": 1.0, "A2": 2.0, "A1": 3.0}
MORNING_FEATURES = (
    "race_no",
    "event_day",
    "month",
    "lane1_escape_rate",
    "lane1_sashare_rate",
    "lane1_makurare_rate",
    "lane1_makuri_sashi_rate",
    "lane1_grade",
    "lane1_avg_st",
    "lane1_local_st",
    "lane1_nat_win",
    "lane1_nat_2",
    "lane1_nat_3",
    "lane1_local_win",
    "lane1_local_2",
    "lane1_local_3",
    "lane1_motor_2",
    "lane1_motor_3",
    "lane1_motor_recent_2",
    "lane1_motor_recent_3",
    "lane1_boat_2",
    "lane1_boat_3",
    "lane1_season_runs",
    "lane1_season_avg_st",
    "lane1_season_win_rate",
    "lane1_season_top2_rate",
    "lane1_season_top3_rate",
    "attack_nat_win_mean",
    "attack_nat_win_max",
    "attack_nat_2_mean",
    "attack_nat_3_mean",
    "attack_local_win_mean",
    "attack_local_win_max",
    "attack_avg_st_mean",
    "attack_boaters_local_avg_st_mean",
    "attack_motor_2_mean",
    "attack_motor_3_mean",
    "attack_boat_2_mean",
    "attack_boat_3_mean",
    "attack_boaters_sashi_rate_max",
    "attack_boaters_makuri_rate_max",
    "attack_boaters_makuri_sashi_rate_max",
    "attack_boaters_nigashi_rate_mean",
    "attack_a1_count",
    "attack_a_count",
)
FINAL_ONLY_FEATURES = (
    *(f"exhibition_lane{lane}" for lane in LANES),
    *(f"exhibition_gap_lane{lane}" for lane in LANES),
    *(f"exhibition_mean_gap_lane{lane}" for lane in LANES),
    "lane1_exhibition_rank",
    "entry_changed",
    "wind_speed",
    "wave_height",
    "air_temperature",
    "water_temperature",
    "wind_direction",
)


def number(value: Any) -> float:
    if value in (None, "", "-"):
        return math.nan
    try:
        return float(str(value).replace("%", "").replace("位", "").strip())
    except (TypeError, ValueError):
        return math.nan


def finite_mean(values: Iterable[Any]) -> float:
    parsed = [number(value) for value in values]
    parsed = [value for value in parsed if math.isfinite(value)]
    return float(np.mean(parsed)) if parsed else math.nan


def finite_max(values: Iterable[Any]) -> float:
    parsed = [number(value) for value in values]
    parsed = [value for value in parsed if math.isfinite(value)]
    return max(parsed) if parsed else math.nan


def season_features(racer: dict) -> dict[str, float]:
    runs = racer.get("season_runs") or []
    finishes = [number(str(run.get("finish") or "").replace("着", "")) for run in runs]
    finishes = [value for value in finishes if math.isfinite(value)]
    return {
        "season_runs": float(len(finishes)),
        "season_avg_st": finite_mean(run.get("st") for run in runs),
        "season_win_rate": (
            100.0 * sum(value == 1 for value in finishes) / len(finishes)
            if finishes
            else math.nan
        ),
        "season_top2_rate": (
            100.0 * sum(value <= 2 for value in finishes) / len(finishes)
            if finishes
            else math.nan
        ),
        "season_top3_rate": (
            100.0 * sum(value <= 3 for value in finishes) / len(finishes)
            if finishes
            else math.nan
        ),
    }


def racer_features(prefix: str, racer: dict) -> dict[str, float]:
    recent = racer.get("motor_recent") or {}
    values = {
        f"{prefix}_grade": GRADE.get(str(racer.get("class") or "").upper(), math.nan),
        f"{prefix}_avg_st": number(racer.get("avg_st")),
        f"{prefix}_local_st": number(
            racer.get("boaters_local_avg_st") or racer.get("local_st")
        ),
        f"{prefix}_nat_win": number(racer.get("nat_win")),
        f"{prefix}_nat_2": number(racer.get("nat_2")),
        f"{prefix}_nat_3": number(racer.get("nat_3")),
        f"{prefix}_local_win": number(racer.get("local_win")),
        f"{prefix}_local_2": number(racer.get("local_2")),
        f"{prefix}_local_3": number(racer.get("local_3")),
        f"{prefix}_motor_2": number(racer.get("motor_2")),
        f"{prefix}_motor_3": number(racer.get("motor_3")),
        f"{prefix}_motor_recent_2": number(recent.get("top2_rate")),
        f"{prefix}_motor_recent_3": number(recent.get("top3_rate")),
        f"{prefix}_motor_recent_exhibition": number(recent.get("avg_exhibition_time")),
        f"{prefix}_boat_2": number(racer.get("boat_2")),
        f"{prefix}_boat_3": number(racer.get("boat_3")),
    }
    for key, value in season_features(racer).items():
        values[f"{prefix}_{key}"] = value
    return values


def morning_features(document: dict, race: dict) -> dict[str, float]:
    racers = {
        int(racer.get("lane") or 0): racer
        for racer in race.get("racers") or []
        if int(racer.get("lane") or 0) in LANES
    }
    if set(racers) != set(LANES):
        raise ValueError("six_racers_required")
    lane1 = racers[1]
    features: dict[str, float] = {
        "race_no": number(race.get("race")),
        "event_day": number(race.get("eventDay") or document.get("eventDay")),
        "month": number(str(document.get("date") or "")[5:7]),
        "lane1_escape_rate": number(lane1.get("boaters_escape_rate")),
        "lane1_sashare_rate": number(lane1.get("boaters_sashare_rate")),
        "lane1_makurare_rate": number(lane1.get("boaters_makurare_rate")),
        "lane1_makuri_sashi_rate": number(lane1.get("boaters_makurare_zashi_rate")),
    }
    features.update(racer_features("lane1", lane1))
    for lane in range(2, 7):
        features.update(racer_features(f"lane{lane}", racers[lane]))

    attackers = [racers[lane] for lane in range(2, 7)]
    for source in (
        "nat_win",
        "nat_2",
        "nat_3",
        "local_win",
        "local_2",
        "local_3",
        "avg_st",
        "boaters_local_avg_st",
        "motor_2",
        "motor_3",
        "boat_2",
        "boat_3",
        "boaters_sashi_rate",
        "boaters_makuri_rate",
        "boaters_makuri_sashi_rate",
        "boaters_nigashi_rate",
    ):
        values = [racer.get(source) for racer in attackers]
        features[f"attack_{source}_mean"] = finite_mean(values)
        features[f"attack_{source}_max"] = finite_max(values)
    features["attack_a1_count"] = float(
        sum(str(racer.get("class") or "").upper() == "A1" for racer in attackers)
    )
    features["attack_a_count"] = float(
        sum(str(racer.get("class") or "").upper() in {"A1", "A2"} for racer in attackers)
    )
    return features


def load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def final_features(
    base: dict[str, float], direct: dict, exhibition: dict
) -> dict[str, float]:
    features = dict(base)
    direct_data = direct.get("data") or {}
    entries = (exhibition.get("data") or {}).get("entries") or []
    by_lane = {
        int(entry.get("lane") or 0): entry
        for entry in entries
        if int(entry.get("lane") or 0) in LANES
    }
    if set(by_lane) != set(LANES):
        raise ValueError("six_exhibition_entries_required")
    times = [number(by_lane[lane].get("exhibition_time")) for lane in LANES]
    if not all(math.isfinite(value) for value in times):
        raise ValueError("six_exhibition_times_required")
    best = min(times)
    mean = float(np.mean(times))
    for lane, exhibition_time in zip(LANES, times):
        features[f"exhibition_lane{lane}"] = exhibition_time
        features[f"exhibition_gap_lane{lane}"] = exhibition_time - best
        features[f"exhibition_mean_gap_lane{lane}"] = exhibition_time - mean
    features.update(
        {
            "lane1_exhibition_rank": number(by_lane[1].get("exhibition_rank")),
            "entry_changed": float(bool(direct_data.get("entry_changed"))),
            "wind_speed": number(direct_data.get("wind_speed")),
            "wave_height": number(direct_data.get("wave_height")),
            "air_temperature": number(direct_data.get("air_temperature")),
            "water_temperature": number(direct_data.get("water_temperature")),
            "wind_direction": number(direct_data.get("wind_direction")),
        }
    )
    return features


def collect_rows(repo_root: Path) -> tuple[list[dict], list[dict], Counter]:
    venue_dir = repo_root / "data" / "venues" / "tokoname"
    live_root = repo_root / "data" / "live"
    morning_rows: list[dict] = []
    final_rows: list[dict] = []
    missing: Counter = Counter()
    for day_dir in sorted(live_root.iterdir()):
        target_live = day_dir / "tokoname"
        if not target_live.is_dir():
            continue
        target_date = day_dir.name
        document = load_json(venue_dir / f"{target_date.replace('-', '')}.json")
        if document is None:
            missing["morning_document"] += 12
            continue
        races = {int(race.get("race") or 0): race for race in document.get("races") or []}
        for race_no in LANES + tuple(range(7, 13)):
            race = races.get(race_no)
            result = load_json(target_live / f"{race_no:02d}" / "result.json")
            order = ((result or {}).get("data") or {}).get("order") or []
            if race is None or (result or {}).get("complete") is not True or not order:
                missing["race_or_result"] += 1
                continue
            if str(order[0]) not in {str(lane) for lane in LANES}:
                missing["invalid_winner"] += 1
                continue
            try:
                base = morning_features(document, race)
            except ValueError as exc:
                missing[str(exc)] += 1
                continue
            row = {
                "date": target_date,
                "race": race_no,
                "target": int(str(order[0]) != "1"),
                "features": base,
            }
            morning_rows.append(row)
            direct = load_json(target_live / f"{race_no:02d}" / "direct.json")
            exhibition = load_json(target_live / f"{race_no:02d}" / "exhibition.json")
            if (direct or {}).get("complete") is not True:
                missing["direct"] += 1
                continue
            if (exhibition or {}).get("complete") is not True:
                missing["exhibition"] += 1
                continue
            try:
                live_features = final_features(base, direct or {}, exhibition or {})
            except ValueError as exc:
                missing[str(exc)] += 1
                continue
            final_rows.append({**row, "features": live_features})
    return morning_rows, final_rows, missing


def matrix(rows: list[dict], names: list[str] | None = None) -> tuple[np.ndarray, np.ndarray, list[str]]:
    feature_names = names or sorted({key for row in rows for key in row["features"]})
    values = np.asarray(
        [[row["features"].get(name, math.nan) for name in feature_names] for row in rows],
        dtype=float,
    )
    targets = np.asarray([row["target"] for row in rows], dtype=int)
    return values, targets, feature_names


def pipeline(min_samples_leaf: int) -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                RandomForestClassifier(
                    n_estimators=500,
                    max_depth=5,
                    min_samples_leaf=min_samples_leaf,
                    max_features=0.6,
                    n_jobs=-1,
                    random_state=20261010,
                ),
            ),
        ]
    )


def select_min_samples_leaf(x: np.ndarray, y: np.ndarray) -> tuple[int, dict[str, float]]:
    candidates = (10, 20, 30, 40, 60)
    folds = min(5, max(2, len(y) // 60))
    splitter = TimeSeriesSplit(n_splits=folds)
    scores: dict[str, float] = {}
    for candidate in candidates:
        fold_scores = []
        for train_index, test_index in splitter.split(x):
            if len(set(y[train_index])) < 2 or len(set(y[test_index])) < 2:
                continue
            model = pipeline(candidate)
            model.fit(x[train_index], y[train_index])
            probability = model.predict_proba(x[test_index])[:, 1]
            fold_scores.append(brier_score_loss(y[test_index], probability))
        scores[str(candidate)] = float(np.mean(fold_scores)) if fold_scores else math.inf
    selected = min(candidates, key=lambda candidate: scores[str(candidate)])
    return selected, scores


def calibration(y: np.ndarray, probability: np.ndarray) -> list[dict]:
    rows = []
    for lower in np.arange(0.0, 1.0, 0.1):
        upper = lower + 0.1
        mask = (probability >= lower) & (
            probability <= upper if upper >= 1.0 else probability < upper
        )
        if not mask.any():
            continue
        rows.append(
            {
                "range": f"{int(lower * 100)}-{int(upper * 100)}",
                "races": int(mask.sum()),
                "predicted_percent": round(float(probability[mask].mean() * 100), 3),
                "actual_percent": round(float(y[mask].mean() * 100), 3),
            }
        )
    return rows


def metrics(y: np.ndarray, probability: np.ndarray) -> dict:
    return {
        "races": int(len(y)),
        "fly_races": int(y.sum()),
        "brier": round(float(brier_score_loss(y, probability)), 6),
        "auc": round(float(roc_auc_score(y, probability)), 6),
        "log_loss": round(float(log_loss(y, probability, labels=[0, 1])), 6),
        "mean_probability": round(float(probability.mean()), 6),
        "actual_rate": round(float(y.mean()), 6),
        "calibration": calibration(y, probability),
    }


def practical_metrics(rows: list[dict], probability_key: str) -> dict:
    available = [row for row in rows if row.get(probability_key) is not None]
    actual = np.asarray([row["actual_fly"] for row in available], dtype=int)
    probability = np.asarray(
        [row[probability_key] / 100.0 for row in available], dtype=float
    )
    return metrics(actual, probability)


def train_phase(
    rows: list[dict], output_path: Path, feature_names: tuple[str, ...]
) -> tuple[dict, dict]:
    train_rows = [row for row in rows if row["date"] < VALIDATION_START]
    validation_rows = [row for row in rows if row["date"] >= VALIDATION_START]
    if len(train_rows) < 200 or len(validation_rows) < 100:
        raise RuntimeError(
            f"insufficient_time_split: train={len(train_rows)} validation={len(validation_rows)}"
        )
    names = list(feature_names)
    x_train, y_train, names = matrix(train_rows, names)
    x_validation, y_validation, _ = matrix(validation_rows, names)
    selected_min_samples_leaf, cv_scores = select_min_samples_leaf(x_train, y_train)
    model = pipeline(selected_min_samples_leaf)
    model.fit(x_train, y_train)
    probability = model.predict_proba(x_validation)[:, 1]
    evaluation = metrics(y_validation, probability)
    artifact = {
        "version": MODEL_VERSION,
        "trained_through": max(row["date"] for row in train_rows),
        "validation_start": VALIDATION_START,
        "feature_names": names,
        "selected_min_samples_leaf": selected_min_samples_leaf,
        "pipeline": model,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, output_path)
    details = {
        "train": {
            "start": min(row["date"] for row in train_rows),
            "end": max(row["date"] for row in train_rows),
            "races": len(train_rows),
            "fly_races": int(y_train.sum()),
        },
        "validation": {
            "start": min(row["date"] for row in validation_rows),
            "end": max(row["date"] for row in validation_rows),
            **evaluation,
        },
        "feature_count": len(names),
        "selected_min_samples_leaf": selected_min_samples_leaf,
        "training_cv_brier": cv_scores,
    }
    predictions = {
        f"{row['date']}-{row['race']:02d}": round(float(value * 100), 3)
        for row, value in zip(validation_rows, probability)
    }
    return details, predictions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "models")
    parser.add_argument("--report", type=Path, default=Path(__file__).resolve().parent / "lane1_fly_v1_validation.json")
    args = parser.parse_args()

    morning_rows, final_rows, missing = collect_rows(args.repo_root.resolve())
    morning, morning_predictions = train_phase(
        morning_rows,
        args.output_dir / "tokoname_lane1_fly_morning_v1.joblib",
        MORNING_FEATURES,
    )
    final, final_predictions = train_phase(
        final_rows,
        args.output_dir / "tokoname_lane1_fly_final_v1.joblib",
        MORNING_FEATURES + FINAL_ONLY_FEATURES,
    )
    recent_keys = sorted(
        key for key in morning_predictions if "2026-10-06" <= key[:10] <= "2026-10-10"
    )
    actual_by_key = {
        f"{row['date']}-{row['race']:02d}": row["target"] for row in morning_rows
    }
    recent = []
    for key in recent_keys:
        recent.append(
            {
                "race": key,
                "actual_fly": actual_by_key[key],
                "morning_percent": morning_predictions[key],
                "final_percent": final_predictions.get(key),
            }
        )
    recent_summary = {
        "morning": practical_metrics(recent, "morning_percent"),
        "final": practical_metrics(recent, "final_percent"),
        "by_date": {},
    }
    for target_date in sorted({row["race"][:10] for row in recent}):
        date_rows = [row for row in recent if row["race"].startswith(target_date)]
        recent_summary["by_date"][target_date] = {
            "morning": practical_metrics(date_rows, "morning_percent"),
            "final": practical_metrics(date_rows, "final_percent"),
        }
    report = {
        "version": MODEL_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "label": "lane_1_not_first",
        "prohibited_inputs": ["odds", "race_actual_st", "result", "original_exhibition"],
        "source_period": {
            "start": min(row["date"] for row in morning_rows),
            "end": max(row["date"] for row in morning_rows),
            "morning_races": len(morning_rows),
            "final_races": len(final_rows),
        },
        "missing": dict(sorted(missing.items())),
        "morning": morning,
        "final": final,
        "recent_2026_10_06_to_10": recent,
        "recent_2026_10_06_to_10_summary": recent_summary,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
