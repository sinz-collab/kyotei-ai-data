from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

try:
    from .train_lane1_fly_v1 import final_features, morning_features
except ImportError:
    from train_lane1_fly_v1 import final_features, morning_features


MODEL_VERSION = "tokoname_lane1_fly_v1"
MODEL_FILES = {
    "morning": "tokoname_lane1_fly_morning_v1.joblib",
    "post_exhibition": "tokoname_lane1_fly_final_v1.joblib",
}


@lru_cache(maxsize=2)
def load_artifact(path: str) -> dict:
    artifact = joblib.load(path)
    if artifact.get("version") != MODEL_VERSION:
        raise ValueError(f"lane1_fly_model_version_invalid: {path}")
    if not artifact.get("feature_names") or artifact.get("pipeline") is None:
        raise ValueError(f"lane1_fly_model_invalid: {path}")
    return artifact


def level(probability: float) -> str:
    if probability >= 60.0:
        return "危険"
    if probability >= 50.0:
        return "警戒"
    if probability >= 40.0:
        return "注意"
    return "通常"


def predict_features(features: dict[str, float], phase: str, model_dir: Path) -> dict:
    artifact = load_artifact(str((model_dir / MODEL_FILES[phase]).resolve()))
    names = list(artifact["feature_names"])
    missing = [
        name
        for name in names
        if name not in features
        or not math.isfinite(float(features.get(name, math.nan)))
    ]
    values = np.asarray(
        [[features.get(name, math.nan) for name in names]],
        dtype=float,
    )
    probability = float(artifact["pipeline"].predict_proba(values)[0, 1] * 100.0)
    probability = round(max(0.0, min(100.0, probability)), 2)
    return {
        "lane1FlyProbability": probability,
        "lane1FlyLevel": level(probability),
        "lane1FlyStage": phase,
        "lane1FlyDetail": {
            "probability": probability,
            "level": level(probability),
            "stage": phase,
            "model": MODEL_VERSION,
            "trainedThrough": artifact.get("trained_through"),
            "featureCount": len(names),
            "missingFeatures": missing,
            "oddsUsedForPrediction": False,
            "raceActualStartUsedForPrediction": False,
            "resultUsedForPrediction": False,
            "originalExhibitionUsedForPrediction": False,
        },
    }


def predict_morning(document: dict, race: dict, model_dir: Path) -> dict:
    return predict_features(morning_features(document, race), "morning", model_dir)


def predict_post_exhibition(
    document: dict,
    race: dict,
    direct: dict,
    exhibition: dict,
    model_dir: Path,
) -> dict:
    base = morning_features(document, race)
    features = final_features(base, direct, exhibition)
    return predict_features(features, "post_exhibition", model_dir)


def attach(prediction: dict, fly_prediction: dict) -> None:
    prediction.update(fly_prediction)
