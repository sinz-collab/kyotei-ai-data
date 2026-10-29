from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import logsumexp
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler

from naruto_morning_model import FEATURES as MORNING_FEATURES
from naruto_morning_model import load_training_data, train_models


BASE_COLUMNS = [
    "national_win_rate",
    "national_2rate_pct",
    "local_win_rate",
    "local_2rate_pct",
    "motor_2rate_pct",
    "boat_2rate_pct",
    "lane",
    "lap_time",
    "turn_time",
    "straight_time",
]
METHODS = ["逃げ", "差し", "まくり", "まくり差し"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reference(bundle: Path, fold: str) -> tuple[dict, dict]:
    source_name = "naruto_v5_early.py" if fold == "early" else "naruto_v5_late.py"
    replay_date = "2026-10-06" if fold == "early" else "2026-10-21"
    source = (bundle / source_name).read_text(encoding="utf-8")
    scope = {"__name__": "__artifact_training__"}
    exec(compile(source.split("\nrows=[]\n", 1)[0], source_name, "exec"), scope)
    replay_source = (bundle / "naruto_oct09_water_retest.py").read_text(encoding="utf-8")
    replay_prefix = replay_source.split("rows=[];summ=[]", 1)[0]
    replay = {"__name__": "__artifact_training__"}
    replay_code = replay_prefix.replace("'2026-10-09'", repr(replay_date))
    if fold == "early":
        replay_code = replay_code.replace(
            "assert len(ix)==12 and len(weather)==12, (len(ix),len(weather))",
            "weather=weather[weather.race_no.isin(r.iloc[ix].race_no)];assert len(weather)==len(ix)",
        )
    exec(compile(replay_code, "replay", "exec"), replay)
    return scope, replay


def fit_stage_artifacts(replay: dict) -> dict:
    base = replay["base"]
    full = replay["full"]
    train = replay["train"]
    actual = replay["actual"]

    def fit(features: np.ndarray, stages: tuple[int, ...]) -> dict:
        n, _, width = features.shape
        flat = features.reshape(-1, width)
        train_rows = (train[:, None] * 6 + np.arange(6)[None, :]).ravel()
        imputer = SimpleImputer(strategy="median", keep_empty_features=True)
        scaler = StandardScaler()
        transformed = scaler.fit_transform(imputer.fit_transform(flat[train_rows]))
        all_rows = scaler.transform(imputer.transform(flat)).reshape(n, 6, -1)
        mask = np.ones((len(train), 6), dtype=bool)
        weights: dict[int, np.ndarray] = {}
        for stage in range(3):
            target = actual[train, stage]
            z = all_rows[train]
            if stage in stages:
                def objective(weight: np.ndarray) -> tuple[float, np.ndarray]:
                    logits = z @ weight
                    masked = np.where(mask, logits, -1e6)
                    partition = logsumexp(masked, axis=1)
                    probability = np.exp(masked - partition[:, None])
                    loss = np.mean(partition - logits[np.arange(len(train)), target]) + 0.025 * (weight @ weight)
                    gradient = (
                        np.mean(
                            np.einsum("nk,nkd->nd", probability, z)
                            - z[np.arange(len(train)), target],
                            axis=0,
                        )
                        + 0.05 * weight
                    )
                    return float(loss), gradient

                fitted = minimize(
                    objective,
                    np.zeros(all_rows.shape[-1]),
                    jac=True,
                    method="L-BFGS-B",
                    options={"maxiter": 85},
                )
                weights[stage] = fitted.x
            mask[np.arange(len(train)), target] = False
        return {"imputer": imputer, "scaler": scaler, "weights": weights}

    return {
        "base": fit(base, (1, 2)),
        "full": fit(full, (0,)),
    }


def player_daily_history(replay: dict) -> pd.DataFrame:
    hr = replay["hr"].copy()
    he = replay["he"].copy()
    hr["winner_lane"] = pd.to_numeric(
        hr.trifecta_combo.astype(str).str.extract(r"^([1-6])-")[0], errors="coerce"
    )
    wins = hr[["race_id", "date", "winner_lane", "kimarite"]].merge(
        he[["race_id", "lane", "player_id"]],
        left_on=["race_id", "winner_lane"],
        right_on=["race_id", "lane"],
        how="left",
    ).dropna(subset=["player_id"])
    all_races = he[["date", "player_id", "race_id"]].dropna(subset=["player_id"]).drop_duplicates()
    all_races["player_id"] = pd.to_numeric(all_races.player_id, errors="coerce")
    wins["player_id"] = pd.to_numeric(wins.player_id, errors="coerce")
    wins["win"] = 1
    for method in METHODS:
        wins[f"win_{method}"] = (wins.kimarite == method).astype(int)
    wins_by_day = wins.groupby(["player_id", "date"], as_index=False)[
        ["win"] + [f"win_{method}" for method in METHODS]
    ].sum()
    races_by_day = (
        all_races.groupby(["player_id", "date"], as_index=False)
        .size()
        .rename(columns={"size": "starts"})
    )
    daily = races_by_day.merge(wins_by_day, on=["player_id", "date"], how="left").fillna(0)
    daily = daily.sort_values(["player_id", "date"])
    for column in ["starts", "win"] + [f"win_{method}" for method in METHODS]:
        daily[f"cumulative_{column}"] = daily.groupby("player_id")[column].cumsum()
    return daily[["player_id", "date"] + [f"cumulative_{column}" for column in ["starts", "win"] + [f"win_{method}" for method in METHODS]]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fold", choices=("early", "late"), default="late")
    args = parser.parse_args()
    reference, replay = load_reference(args.bundle, args.fold)
    morning_models = None
    morning_features = None
    if args.fold == "late":
        morning_results, morning_wide = load_training_data(
            args.bundle / "naruto_live_analysis_20251027_20261026.zip"
        )
        morning_models = train_models(
            morning_results[morning_results.date < "2026-10-21"], morning_wide
        )
        morning_features = list(MORNING_FEATURES)
    # Keep the source's point-in-time history. Historical replay filters strictly
    # to rows before each target date; production is frozen at the archive end.
    history = replay["history"].copy()
    artifact = {
        "model_version": (
            "naruto_v5_live_alpha050_6211_replay_20261005"
            if args.fold == "early"
            else "naruto_v5_live_alpha050_6211_fixed_20261020"
        ),
        "alpha": 0.5,
        "allocation": [6, 2, 1, 1, 0],
        "fixed_cutoff": "2026-10-06" if args.fold == "early" else "2026-10-21",
        "base_columns": BASE_COLUMNS,
        "conditional_models": reference["models"],
        "morning_models": morning_models,
        "morning_features": morning_features,
        "stage_artifacts": fit_stage_artifacts(replay),
        "pair_ratio": replay["ratio"],
        "method_prior": replay["method_prior"],
        "player_daily_history": player_daily_history(replay),
        "triple_history": history[["date", "head", "second", "third", "kimarite"]],
        "source_sha256": {
            name: sha256(args.bundle / name)
            for name in (
                "naruto_v5_early.py" if args.fold == "early" else "naruto_v5_late.py",
                "naruto_oct09_water_retest.py",
                "naruto_live_analysis_20251027_20261026.zip",
                "naruto_chatgpt_analysis_20231027_20261026.zip",
                "naruto_slit_left_neighbor_features.csv",
            )
        },
        "runtime_versions": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scipy": __import__("scipy").__version__,
            "scikit_learn": __import__("sklearn").__version__,
            "joblib": joblib.__version__,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, args.output, compress=3)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
