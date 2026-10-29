from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import permutations
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np
import pandas as pd
from scipy.special import logsumexp


ORDERS = np.array(list(permutations(range(6), 3)))
METHODS = ["逃げ", "差し", "まくり", "まくり差し"]
CONDITIONAL_COLUMNS = [
    "national_win_rate",
    "national_2rate_pct",
    "local_win_rate",
    "local_2rate_pct",
    "motor_2rate_pct",
    "grade_score",
    "exhibition_st",
    "turn_time",
    "straight_time",
    "lap_time",
]
REQUIRED_LIVE_COLUMNS = [
    "exhibition_st",
    "exhibition_course",
    "turn_time",
    "straight_time",
    "lap_time",
]


@dataclass(frozen=True)
class NarutoPrediction:
    model_version: str
    generated_at: str
    stage: str
    probabilities: list[dict]
    first: list[float]
    second: list[float]
    third: list[float]
    normal_tickets: list[dict]
    fly_tickets: list[dict]
    fly_probability: float
    input_snapshot: dict


class NarutoV5Engine:
    def __init__(self, artifact_path: Path | str):
        self.artifact = joblib.load(artifact_path)

    @staticmethod
    def _as_number(value: object) -> float:
        try:
            return float(str(value).replace("%", "").strip())
        except (TypeError, ValueError):
            return np.nan

    def _player_extra(self, player_id: int, target_date: str) -> np.ndarray:
        history = self.artifact["player_daily_history"]
        rows = history[
            (pd.to_numeric(history.player_id, errors="coerce") == int(player_id))
            & (history.date.astype(str) < target_date)
        ]
        if rows.empty:
            values = {column: 0.0 for column in ["starts", "win"] + [f"win_{method}" for method in METHODS]}
        else:
            latest = rows.iloc[-1]
            values = {
                column: float(latest[f"cumulative_{column}"])
                for column in ["starts", "win"] + [f"win_{method}" for method in METHODS]
            }
        starts = values["starts"]
        wins = values["win"]
        method_wins = np.array([values[f"win_{method}"] for method in METHODS])
        return np.concatenate(
            [
                [np.log1p(starts), np.log1p(wins)],
                method_wins / (starts + 30.0),
                method_wins / (wins + 10.0),
            ]
        )

    def _base_features(
        self,
        racers: list[dict],
        live: list[dict],
        target_date: str,
        *,
        require_complete_live: bool,
    ) -> tuple[np.ndarray, np.ndarray]:
        by_lane = {int(row["lane"]): row for row in live}
        if set(by_lane) != set(range(1, 7)):
            raise ValueError("naruto_live_entries_must_cover_six_lanes")
        matrix: list[list[float]] = []
        extra: list[np.ndarray] = []
        racers_by_lane = {int(row["lane"]): row for row in racers}
        if set(racers_by_lane) != set(range(1, 7)):
            raise ValueError("naruto_racers_must_cover_six_lanes")
        for lane in range(1, 7):
            racer = racers_by_lane[lane]
            current = by_lane[lane]
            missing = [column for column in REQUIRED_LIVE_COLUMNS if not np.isfinite(self._as_number(current.get(column)))]
            if missing and require_complete_live:
                raise ValueError(f"naruto_live_features_missing_lane_{lane}:{','.join(missing)}")
            matrix.append(
                [
                    self._as_number(racer.get("national_win_rate")),
                    self._as_number(racer.get("national_2rate_pct")),
                    self._as_number(racer.get("local_win_rate")),
                    self._as_number(racer.get("local_2rate_pct")),
                    self._as_number(racer.get("motor_2rate_pct")),
                    self._as_number(racer.get("boat_2rate_pct")),
                    float(lane),
                    self._as_number(current.get("lap_time")),
                    self._as_number(current.get("turn_time")),
                    self._as_number(current.get("straight_time")),
                ]
            )
            extra.append(self._player_extra(int(racer.get("player_id") or 0), target_date))
        values = np.asarray(matrix, dtype=float)
        for column in (7, 8, 9):
            values[:, column] -= np.nanmean(values[:, column])
        return values, np.asarray(extra)

    @staticmethod
    def _scores(artifact: dict, features: np.ndarray, stage: int) -> np.ndarray:
        transformed = artifact["scaler"].transform(artifact["imputer"].transform(features))
        return transformed @ artifact["weights"][stage]

    def _head_base(self, base: np.ndarray, extra: np.ndarray) -> np.ndarray:
        stages = self.artifact["stage_artifacts"]
        scores = [
            self._scores(stages["full"], np.concatenate([base, extra], axis=1), 0),
            self._scores(stages["base"], base, 1),
            self._scores(stages["base"], base, 2),
        ]
        probability = np.ones(120)
        for stage in range(3):
            for index, order in enumerate(ORDERS):
                available = [lane for lane in range(6) if lane not in order[:stage]]
                probability[index] *= np.exp(
                    scores[stage][order[stage]] - logsumexp(scores[stage][available])
                )
        probability /= probability.sum()
        ratio = self.artifact["pair_ratio"]
        prior = self.artifact["method_prior"]
        method_probability = np.zeros((6, 5))
        for lane in range(6):
            skill = np.maximum(extra[lane, 2:6], 0)
            skill = skill / max(float(skill.sum()), 1e-8)
            starts = np.expm1(extra[lane, 0])
            reliability = np.clip(starts / (starts + 40), 0, 0.55)
            method_probability[lane, :4] = (1 - reliability) * prior[lane, :4] + reliability * skill
            method_probability[lane, 4] = (1 - reliability) * prior[lane, 4]
            method_probability[lane] /= method_probability[lane].sum()
        lift = np.einsum("ak,akbc->abc", method_probability, ratio)
        ticket_lift = lift[ORDERS[:, 0], ORDERS[:, 1], ORDERS[:, 2]]
        probability *= np.power(ticket_lift, 0.5)
        return probability / probability.sum()

    def _historical_adjustment(self, probability: np.ndarray, extra: np.ndarray, target_date: str) -> np.ndarray:
        history = self.artifact["triple_history"]
        cut = pd.Timestamp(target_date)
        window = history[
            (pd.to_datetime(history.date) >= cut - pd.Timedelta(days=365))
            & (pd.to_datetime(history.date) < cut)
        ]
        n2 = np.zeros((6, 6))
        n3 = np.zeros((6, 6, 6))
        for head, second, third in window[["head", "second", "third"]].itertuples(index=False, name=None):
            n2[int(head), int(second)] += 1
            n3[int(head), int(second), int(third)] += 1
        target2 = np.zeros((6, 6))
        target3 = np.zeros((6, 6, 6))
        for head in range(1, 6):
            seconds = [lane for lane in range(6) if lane != head]
            target2[head, seconds] = (n2[head, seconds] + 4) / (n2[head].sum() + 20)
            for second in seconds:
                thirds = [lane for lane in range(6) if lane not in (head, second)]
                target3[head, second, thirds] = (
                    n3[head, second, thirds] + 3
                ) / (n3[head, second].sum() + 12)
        base2 = np.zeros((6, 6))
        base3 = np.zeros((6, 6, 6))
        for head in range(1, 6):
            head_mask = ORDERS[:, 0] == head
            head_mass = probability[head_mask].sum()
            for second in range(6):
                if second == head:
                    continue
                pair_mask = head_mask & (ORDERS[:, 1] == second)
                pair_mass = probability[pair_mask].sum()
                base2[head, second] = pair_mass / head_mass if head_mass else 0
                for third in range(6):
                    if third in (head, second):
                        continue
                    triple_mask = pair_mask & (ORDERS[:, 2] == third)
                    base3[head, second, third] = probability[triple_mask].sum() / pair_mass if pair_mass else 0
        adjusted = probability.copy()
        for index, (head, second, third) in enumerate(ORDERS):
            if head == 0:
                continue
            adjusted[index] *= np.clip(target2[head, second] / max(base2[head, second], 1e-8), 0.25, 4)
            adjusted[index] *= np.clip(target3[head, second, third] / max(base3[head, second, third], 1e-8), 0.25, 4)
        for head in range(1, 6):
            mask = ORDERS[:, 0] == head
            adjusted[mask] *= probability[mask].sum() / adjusted[mask].sum()
            masses = np.array([adjusted[mask & (ORDERS[:, 1] == second)].sum() for second in range(6)])
            tempered = masses ** 0.85
            tempered[head] = 0
            desired = tempered / tempered.sum() * masses.sum()
            for second in range(6):
                if second == head or masses[second] == 0:
                    continue
                pair = mask & (ORDERS[:, 1] == second)
                adjusted[pair] *= desired[second] / masses[second]
        for head in range(1, 6):
            historical_rate = np.maximum(extra[head, 2:6], 0).sum()
            factor = np.clip(np.exp(0.3 * (np.clip(historical_rate, 0, 0.5) - 0.08) / 0.15), 0.8, 1.25)
            adjusted[ORDERS[:, 0] == head] *= factor
        return adjusted / adjusted.sum()

    @staticmethod
    def _conditional_row(racers: list[dict], live: list[dict]) -> pd.Series:
        live_by_lane = {int(row["lane"]): row for row in live}
        values: dict[str, float] = {}
        grade = {"A1": 3, "A2": 2, "B1": 1, "B2": 0}
        for racer in racers:
            lane = int(racer["lane"])
            for column in (
                "national_win_rate",
                "national_2rate_pct",
                "local_win_rate",
                "local_2rate_pct",
                "motor_2rate_pct",
            ):
                values[f"{column}_{lane}"] = NarutoV5Engine._as_number(racer.get(column))
            values[f"grade_score_{lane}"] = float(grade.get(str(racer.get("class") or ""), 1))
            for column in REQUIRED_LIVE_COLUMNS:
                values[f"{column}_{lane}"] = NarutoV5Engine._as_number(live_by_lane[lane].get(column))
        return pd.Series(values)

    @staticmethod
    def _candidate_features(row: pd.Series, head: int, second: int, candidate: int) -> list[float]:
        def value(column: str, lane: int) -> float:
            if not lane:
                return np.nan
            try:
                return float(row.get(f"{column}_{lane}", np.nan))
            except (TypeError, ValueError):
                return np.nan
        features = [float(candidate), float(head), float(second), float(candidate - head), float(candidate - second)]
        for column in CONDITIONAL_COLUMNS:
            own = value(column, candidate)
            features.extend([own, own - value(column, head), own - value(column, second)])
        features.append(value("exhibition_st", candidate - 1) - value("exhibition_st", candidate))
        return features

    def _role_probability(self, model: object, row: pd.Series, head: int, second: int, role: int) -> dict[int, float]:
        candidates = [lane for lane in range(1, 7) if lane != head and (role == 2 or lane != second)]
        features = np.asarray(
            [self._candidate_features(row, head, second, lane) for lane in candidates], dtype=float
        )
        score = np.maximum(model.predict_proba(features)[:, 1], 1e-8)
        score /= score.sum()
        return dict(zip(candidates, score))

    def _morning_row(self, racers: list[dict]) -> pd.Series:
        grade = {"A1": 3, "A2": 2, "B1": 1, "B2": 0}
        values: dict[str, float] = {}
        for racer in racers:
            lane = int(racer["lane"])
            for column in self.artifact["morning_features"]:
                value = grade.get(str(racer.get("class") or ""), 1) if column == "grade_score" else self._as_number(racer.get(column))
                values[f"{column}_{lane}"] = float(value)
        return pd.Series(values)

    def _morning_candidate_features(
        self, row: pd.Series, head: int, second: int, candidate: int
    ) -> list[float]:
        values = [float(candidate), float(head), float(second), float(candidate - head), float(candidate - second)]
        for column in self.artifact["morning_features"]:
            field = np.array([float(row.get(f"{column}_{lane}", np.nan)) for lane in range(1, 7)])
            average = float(np.nanmean(field)) if np.isfinite(field).any() else np.nan
            own = float(row.get(f"{column}_{candidate}", np.nan))
            head_value = float(row.get(f"{column}_{head}", average)) if head else average
            second_value = float(row.get(f"{column}_{second}", average)) if second else average
            values.extend([own, own - average, own - head_value, own - second_value])
        return values

    def _morning_role_probability(
        self, model: object, row: pd.Series, head: int, second: int, role: int
    ) -> dict[int, float]:
        lanes = [
            lane
            for lane in range(1, 7)
            if (role == 1 or lane != head) and (role != 3 or lane != second)
        ]
        features = np.asarray(
            [self._morning_candidate_features(row, head, second, lane) for lane in lanes],
            dtype=float,
        )
        score = np.maximum(model.predict_proba(features)[:, 1], 1e-12)
        score /= score.sum()
        return dict(zip(lanes, score))

    def predict_morning(
        self,
        *,
        date: str,
        race_no: int,
        racers: list[dict],
        generated_at: str | None = None,
    ) -> NarutoPrediction:
        models = self.artifact.get("morning_models")
        if not models:
            raise RuntimeError("naruto_morning_model_unavailable")
        row = self._morning_row(racers)
        first_probability = self._morning_role_probability(models[1], row, 0, 0, 1)
        probability = np.zeros(120)
        for index, (head0, second0, third0) in enumerate(ORDERS):
            head, second_lane, third_lane = head0 + 1, second0 + 1, third0 + 1
            second_probability = self._morning_role_probability(models[2], row, head, 0, 2)
            third_probability = self._morning_role_probability(models[3], row, head, second_lane, 3)
            probability[index] = (
                first_probability[head]
                * second_probability[second_lane]
                * third_probability[third_lane]
            )
        probability /= probability.sum()
        first = [float(probability[ORDERS[:, 0] == lane].sum()) for lane in range(6)]
        second = [float(probability[ORDERS[:, 1] == lane].sum()) for lane in range(6)]
        third = [float(probability[ORDERS[:, 2] == lane].sum()) for lane in range(6)]
        normal = np.argsort(-probability, kind="stable")[:10]
        head_order = sorted(range(1, 6), key=lambda head: (-first[head], head))
        fly: list[int] = []
        for head, count in zip(head_order, self.artifact["allocation"]):
            subset = np.flatnonzero(ORDERS[:, 0] == head)
            fly.extend(subset[np.argsort(-probability[subset], kind="stable")[:count]])
        rows = [
            {"combo": "-".join(str(lane + 1) for lane in ORDERS[index]), "probability": float(probability[index])}
            for index in range(120)
        ]
        timestamp = generated_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
        return NarutoPrediction(
            model_version="naruto_morning_independent_v1_fixed_20261020",
            generated_at=timestamp,
            stage="morning",
            probabilities=rows,
            first=first,
            second=second,
            third=third,
            normal_tickets=[rows[int(index)].copy() for index in normal],
            fly_tickets=[rows[int(index)].copy() for index in fly],
            fly_probability=float(1 - first[0]),
            input_snapshot={"date": date, "race": race_no, "racers": racers},
        )

    def predict_live(
        self,
        *,
        date: str,
        race_no: int,
        racers: list[dict],
        live_entries: list[dict],
        wind_speed_mps: float,
        wave_height_cm: float,
        generated_at: str | None = None,
        allow_reference_missing: bool = False,
    ) -> NarutoPrediction:
        base_features, extra = self._base_features(
            racers,
            live_entries,
            date,
            require_complete_live=not allow_reference_missing,
        )
        base = self._historical_adjustment(self._head_base(base_features, extra), extra, date)
        if float(wind_speed_mps) <= 2 and float(wave_height_cm) <= 2:
            base[ORDERS[:, 0] == 0] *= np.exp(0.35)
            base /= base.sum()
        first_mass = np.array([base[ORDERS[:, 0] == lane].sum() for lane in range(6)])
        row = self._conditional_row(racers, live_entries)
        models = self.artifact["conditional_models"]
        candidate = np.zeros(120)
        for head in range(1, 7):
            second = self._role_probability(models[2], row, head, 0, 2)
            for second_lane in range(1, 7):
                if second_lane == head:
                    continue
                third = self._role_probability(models[3], row, head, second_lane, 3)
                for third_lane in range(1, 7):
                    if third_lane in (head, second_lane):
                        continue
                    index = int(np.flatnonzero(np.all(ORDERS == np.array([head, second_lane, third_lane]) - 1, axis=1))[0])
                    candidate[index] = first_mass[head - 1] * second[second_lane] * third[third_lane]
        probability = (1 - self.artifact["alpha"]) * base + self.artifact["alpha"] * candidate
        probability /= probability.sum()
        normal = np.argsort(-probability, kind="stable")[:10]
        head_order = sorted(range(1, 6), key=lambda head: (-first_mass[head], head))
        fly: list[int] = []
        for head, count in zip(head_order, self.artifact["allocation"]):
            subset = np.flatnonzero(ORDERS[:, 0] == head)
            fly.extend(subset[np.argsort(-probability[subset], kind="stable")[:count]])
        first = [float(probability[ORDERS[:, 0] == lane].sum()) for lane in range(6)]
        second = [float(probability[ORDERS[:, 1] == lane].sum()) for lane in range(6)]
        third = [float(probability[ORDERS[:, 2] == lane].sum()) for lane in range(6)]
        timestamp = generated_at or datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
        rows = [
            {"combo": "-".join(str(lane + 1) for lane in ORDERS[index]), "probability": float(probability[index])}
            for index in range(120)
        ]
        ticket = lambda index: rows[int(index)].copy()
        return NarutoPrediction(
            model_version=self.artifact["model_version"],
            generated_at=timestamp,
            stage="live",
            probabilities=rows,
            first=first,
            second=second,
            third=third,
            normal_tickets=[ticket(index) for index in normal],
            fly_tickets=[ticket(index) for index in fly],
            fly_probability=float(1 - first[0]),
            input_snapshot={
                "date": date,
                "race": race_no,
                "racers": racers,
                "liveEntries": live_entries,
                "windSpeedMps": wind_speed_mps,
                "waveHeightCm": wave_height_cm,
            },
        )
