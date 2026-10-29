from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "national_win_rate",
    "national_2rate_pct",
    "local_win_rate",
    "local_2rate_pct",
    "motor_2rate_pct",
    "boat_2rate_pct",
    "grade_score",
]


def load_training_data(source_zip: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    with zipfile.ZipFile(source_zip) as archive:
        entries = pd.read_csv(archive.open("data/morning_entries.csv"))
        results = pd.read_csv(archive.open("data/result_races.csv"))
    for column in FEATURES[:-1]:
        entries[column] = pd.to_numeric(entries[column], errors="coerce")
    entries["grade_score"] = entries["class"].map({"A1": 3, "A2": 2, "B1": 1, "B2": 0})
    wide = entries.pivot(index="race_id", columns="lane", values=FEATURES)
    wide.columns = [f"{column}_{lane}" for column, lane in wide.columns]
    wide = wide.reset_index().set_index("race_id")
    results = results.dropna(subset=["trifecta_combo"]).copy()
    valid = results.trifecta_combo.astype(str).str.fullmatch(r"[1-6]-[1-6]-[1-6]")
    results = results[valid].copy()
    results[["head", "second", "third"]] = results.trifecta_combo.str.split("-", expand=True).astype(int)
    results = results[
        results[["head", "second", "third"]].nunique(axis=1).eq(3)
        & results.race_id.isin(wide.index)
    ].copy()
    return results, wide


def _value(row: pd.Series, column: str, lane: int) -> float:
    try:
        return float(row.get(f"{column}_{lane}", np.nan))
    except (TypeError, ValueError):
        return np.nan


def candidate_features(row: pd.Series, head: int, second: int, lane: int) -> list[float]:
    values = [float(lane), float(head), float(second), float(lane - head), float(lane - second)]
    for column in FEATURES:
        own = _value(row, column, lane)
        field = np.array([_value(row, column, candidate) for candidate in range(1, 7)])
        average = float(np.nanmean(field)) if np.isfinite(field).any() else np.nan
        head_value = _value(row, column, head) if head else average
        second_value = _value(row, column, second) if second else average
        values.extend([own, own - average, own - head_value, own - second_value])
    return values


def train_models(train: pd.DataFrame, wide: pd.DataFrame) -> dict[int, object]:
    models: dict[int, object] = {}
    for role in (1, 2, 3):
        x: list[list[float]] = []
        y: list[int] = []
        for race in train.itertuples(index=False):
            row = wide.loc[race.race_id]
            for lane in range(1, 7):
                if role >= 2 and lane == race.head:
                    continue
                if role == 3 and lane == race.second:
                    continue
                head = race.head if role >= 2 else 0
                second = race.second if role == 3 else 0
                target = race.head if role == 1 else race.second if role == 2 else race.third
                x.append(candidate_features(row, head, second, lane))
                y.append(int(lane == target))
        if role in (1, 2):
            model = make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                HistGradientBoostingClassifier(
                    max_iter=85,
                    learning_rate=0.045,
                    max_leaf_nodes=9,
                    min_samples_leaf=95,
                    l2_regularization=4,
                    random_state=17,
                ),
            )
        else:
            model = make_pipeline(
                SimpleImputer(strategy="median", keep_empty_features=True),
                StandardScaler(),
                LogisticRegression(C=0.12, max_iter=350),
            )
        model.fit(np.asarray(x, dtype=float), np.asarray(y))
        models[role] = model
    return models
