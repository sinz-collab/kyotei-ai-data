from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


BASE_ATTACK = {
    "lane1_escape_weakness": 0.42,
    "lane1_loss_pattern": 0.24,
    "best_outer_attack": 0.54,
    "player_course_matchup": 0.32,
}
THRESHOLD = 0.50


def logit(probability):
    probability = max(1e-8, min(1 - 1e-8, float(probability)))
    return math.log(probability / (1 - probability))


def sigmoid(value):
    return 1 / (1 + math.exp(-value))


def probability(row, coefficients):
    score = logit(row["baseline_fly_probability"])
    features = row["candidate"]["features"]
    score += sum(coefficients.get(key, 0.0) * float(features[key]) for key in coefficients)
    return sigmoid(score)


def metrics(rows, coefficients):
    probs = [probability(row, coefficients) for row in rows]
    actual = [int(row["actual_fly"]) for row in rows]
    predicted = [int(value >= THRESHOLD) for value in probs]
    fly_total = sum(actual)
    escape_total = len(rows) - fly_total
    true_fly = sum(p == y == 1 for p, y in zip(predicted, actual))
    true_escape = sum(p == y == 0 for p, y in zip(predicted, actual))
    return {
        "races": len(rows),
        "correct": true_fly + true_escape,
        "accuracy": round((true_fly + true_escape) / len(rows), 8),
        "escape_recall": round(true_escape / escape_total, 8),
        "fly_recall": round(true_fly / fly_total, 8),
        "brier": round(sum((value - y) ** 2 for value, y in zip(probs, actual)) / len(rows), 8),
        "predicted_fly": sum(predicted),
        "actual_fly": fly_total,
    }


def choose(rows, candidates):
    scored = [(metrics(rows, coefficients), coefficients) for coefficients in candidates]
    # Calibration target is Brier; ties prefer better accuracy, then smaller L1.
    return min(scored, key=lambda item: (
        item[0]["brier"], -item[0]["accuracy"], sum(abs(value) for value in item[1].values())
    ))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True)
    parser.add_argument("--holdout", required=True)
    parser.add_argument("--morning-train")
    parser.add_argument("--morning-holdout")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    train = json.loads(Path(args.train).read_text(encoding="utf-8"))["rows"]
    holdout = json.loads(Path(args.holdout).read_text(encoding="utf-8"))["rows"]
    morning_train = json.loads(Path(args.morning_train).read_text(encoding="utf-8"))["rows"] if args.morning_train else None
    morning_holdout = json.loads(Path(args.morning_holdout).read_text(encoding="utf-8"))["rows"] if args.morning_holdout else None

    stages = []
    coefficients = {}
    stages.append({"name": "v2.7", "coefficients": {}, "train": metrics(train, {}), "holdout": metrics(holdout, {})})

    attack_candidates = [
        {key: round(weight * scale, 8) for key, weight in BASE_ATTACK.items()}
        for index in range(0, 51)
        for scale in [index / 100]
    ]
    _, coefficients = choose(train, attack_candidates)
    stages.append({"name": "+kimarite_attack_defense", "coefficients": coefficients, "train": metrics(train, coefficients), "holdout": metrics(holdout, coefficients)})

    sum_candidates = []
    for index in range(-40, 41):
        candidate = dict(coefficients)
        candidate["exhibition_sum_matchup"] = round(index / 200, 8)
        sum_candidates.append(candidate)
    _, coefficients = choose(train, sum_candidates)
    stages.append({"name": "+exhibition_sum", "coefficients": coefficients, "train": metrics(train, coefficients), "holdout": metrics(holdout, coefficients)})

    lap_candidates = []
    for index in range(-40, 41):
        candidate = dict(coefficients)
        candidate["lap_matchup"] = round(index / 200, 8)
        lap_candidates.append(candidate)
    _, coefficients = choose(train, lap_candidates)
    stages.append({"name": "+lap_time", "coefficients": coefficients, "train": metrics(train, coefficients), "holdout": metrics(holdout, coefficients)})

    if morning_train is not None and morning_holdout is not None:
        for stage in stages:
            stage["morning_train"] = metrics(morning_train, stage["coefficients"])
            stage["morning_holdout"] = metrics(morning_holdout, stage["coefficients"])

    report = {
        "model_id": "heiwajima_fly_v6_staged_calibration_20261102",
        "train": "2026-09 LIVE complete 173R",
        "holdout": "2026-10 LIVE saved 77R",
        "threshold": THRESHOLD,
        "feature_transform": "model.py clip[-2,2]",
        "missing": "neutral zero plus missing code",
        "calibrator": "identity after additive logit residual",
        "selection": "sequential minimum train Brier; accuracy/smaller-L1 tie break",
        "stages": stages,
    }
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
