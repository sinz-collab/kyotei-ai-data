from __future__ import annotations

from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .utils import as_float, as_int, norm_reg_no


CAT_LEVELS = {
    "venue": ["芦屋"],
    "wind_direction": [
        "北",
        "北東",
        "北西",
        "南",
        "南東",
        "南西",
        "東",
        "無風",
        "西",
    ],
    "nearest_tide_type": ["high", "low"],
    "grade": ["missing"],
    "tide_phase": ["falling", "rising"],
}


class FeatureBuilder:
    def __init__(self, model_path: str | Path):
        obj = joblib.load(model_path)

        self.feature_cols = list(obj["feature_cols"])
        self.cat_cols = list(obj["cat_cols"])

    @staticmethod
    def _numeric(series):
        return pd.to_numeric(
            series,
            errors="coerce",
        )

    @staticmethod
    def _set_if_exists(
        df: pd.DataFrame,
        supplied: set,
        name: str,
        value,
    ):
        """
        モデルがその特徴量を要求している場合だけ設定する。
        """
        if name not in df.columns:
            return

        df[name] = value
        supplied.add(name)

    def _build_date_features(
        self,
        df: pd.DataFrame,
        race: dict,
        supplied: set,
    ):
        race_date = (
            race.get("race_date")
            or race.get("date")
        )

        if not race_date:
            return

        try:
            dt = datetime.fromisoformat(
                str(race_date)[:10]
            )
        except (TypeError, ValueError):
            return

        self._set_if_exists(
            df,
            supplied,
            "year",
            dt.year,
        )

        self._set_if_exists(
            df,
            supplied,
            "month",
            dt.month,
        )

        self._set_if_exists(
            df,
            supplied,
            "day",
            dt.day,
        )

        # Python weekday:
        # 月=0 ～ 日=6
        self._set_if_exists(
            df,
            supplied,
            "weekday",
            dt.weekday(),
        )

    def _build_entry_features(
        self,
        df: pd.DataFrame,
        supplied: set,
    ):
        if (
            "lane" not in df.columns
            or "entry_course" not in df.columns
        ):
            return

        lane = self._numeric(
            df["lane"]
        ).fillna(0)

        course = self._numeric(
            df["entry_course"]
        ).fillna(lane)

        diff = course - lane

        self._set_if_exists(
            df,
            supplied,
            "entry_minus_lane",
            diff,
        )

        self._set_if_exists(
            df,
            supplied,
            "abs_entry_minus_lane",
            diff.abs(),
        )

        self._set_if_exists(
            df,
            supplied,
            "entry_course_deviation",
            diff,
        )

    def _build_race_summary(
        self,
        df: pd.DataFrame,
        supplied: set,
        source_col: str,
        prefix: str,
    ):
        """
        1レース6艇の mean/min/max を生成する。

        例:
        exhibition_time
          ->
        exhibition_time_race_mean
        exhibition_time_race_min
        exhibition_time_race_max
        """
        if source_col not in df.columns:
            return

        x = self._numeric(
            df[source_col]
        )

        valid = x.dropna()

        if valid.empty:
            return

        values = {
            f"{prefix}_race_mean": float(
                valid.mean()
            ),
            f"{prefix}_race_min": float(
                valid.min()
            ),
            f"{prefix}_race_max": float(
                valid.max()
            ),
        }

        for name, value in values.items():
            self._set_if_exists(
                df,
                supplied,
                name,
                value,
            )

    def _build_exhibition_features(
        self,
        df: pd.DataFrame,
        supplied: set,
    ):
        if "exhibition_time" not in df.columns:
            return

        x = self._numeric(
            df["exhibition_time"]
        )

        valid = (
            x.dropna()
            .sort_values()
            .tolist()
        )

        if not valid:
            return

        best = float(valid[0])

        second = (
            float(valid[1])
            if len(valid) >= 2
            else best
        )

        third = (
            float(valid[2])
            if len(valid) >= 3
            else second
        )

        self._set_if_exists(
            df,
            supplied,
            "exhibition_best",
            best,
        )

        self._set_if_exists(
            df,
            supplied,
            "exhibition_second_best",
            second,
        )

        self._set_if_exists(
            df,
            supplied,
            "exhibition_third_best",
            third,
        )

        self._set_if_exists(
            df,
            supplied,
            "exhibition_gap_top2",
            second - best,
        )

        self._set_if_exists(
            df,
            supplied,
            "exhibition_gap_top3",
            third - best,
        )

        if "exhibition_diff_from_best" in df.columns:
            df[
                "exhibition_diff_from_best"
            ] = x - best

            supplied.add(
                "exhibition_diff_from_best"
            )

        if "exhibition_std_in_race" in df.columns:
            df[
                "exhibition_std_in_race"
            ] = float(
                x.std(ddof=0)
            )

            supplied.add(
                "exhibition_std_in_race"
            )

        # 明確に順位として計算できるフラグのみ生成する。
        if "exhibition_top_flag" in df.columns:
            rank = x.rank(
                ascending=True,
                method="min",
            )

            df[
                "exhibition_top_flag"
            ] = (rank == 1).astype(int)

            supplied.add(
                "exhibition_top_flag"
            )

        if "exhibition_top2_flag" in df.columns:
            rank = x.rank(
                ascending=True,
                method="min",
            )

            df[
                "exhibition_top2_flag"
            ] = (rank <= 2).astype(int)

            supplied.add(
                "exhibition_top2_flag"
            )

        if "exhibition_top3_flag" in df.columns:
            rank = x.rank(
                ascending=True,
                method="min",
            )

            df[
                "exhibition_top3_flag"
            ] = (rank <= 3).astype(int)

            supplied.add(
                "exhibition_top3_flag"
            )

    def _build_start_features(
        self,
        df: pd.DataFrame,
        supplied: set,
    ):
        if "start_timing" not in df.columns:
            return

        x = self._numeric(
            df["start_timing"]
        )

        valid = x.dropna()

        if valid.empty:
            return

        if "start_std" in df.columns:
            df["start_std"] = float(
                valid.std(ddof=0)
            )

            supplied.add(
                "start_std"
            )

    def _build_tide_aliases(
        self,
        df: pd.DataFrame,
        supplied: set,
    ):
        aliases = {
            "high_tide_1_minutes":
                "high_tide_1_time",

            "high_tide_2_minutes":
                "high_tide_2_time",

            "low_tide_1_minutes":
                "low_tide_1_time",

            "low_tide_2_minutes":
                "low_tide_2_time",
        }

        for target, source in aliases.items():
            if (
                target in df.columns
                and source in df.columns
            ):
                value = self._numeric(
                    df[source]
                )

                if value.notna().any():
                    df[target] = value

                    supplied.add(
                        target
                    )

    def build(self, race: dict):
        racers = (
            race.get("racers")
            or race.get("entries")
            or []
        )

        if len(racers) != 6:
            raise ValueError(
                "race requires exactly 6 racers"
            )

        rows = []
        supplied = set()

        common = {}

        weather = race.get("weather")

        if isinstance(weather, dict):
            common.update(weather)
        elif weather not in (None, ""):
            common["weather"] = weather

        tide = race.get("tide")

        if isinstance(tide, dict):
            common.update(tide)

        common.update(
            {
                k: v
                for k, v in race.items()
                if not isinstance(
                    v,
                    (list, dict),
                )
            }
        )

        aliases = {
            "venue": "venue",
            "race_date": "race_date",
            "race_no": "race_no",
            "lane": "lane",
            "reg_no": "reg_no",

            "motor_no": "motor_no",
            "boat_no": "boat_no",

            "exhibition_time":
                "exhibition_time",

            "entry_course":
                "actual_course",

            "start_timing":
                "start_timing",

            "weather":
                "weather",

            "wind_direction":
                "wind_direction",

            "wind_speed_mps":
                "wind_speed",

            "wave_cm":
                "wave_height",

            "grade":
                "class",

            "nearest_tide_type":
                "nearest_tide_type",

            "tide_phase":
                "tide_phase",

            "national_win_rate":
                "nat_win",

            "national_2ren_rate":
                "nat_2",

            "national_3ren_rate":
                "nat_3",

            "local_win_rate":
                "local_win",

            "local_2ren_rate":
                "local_2",

            "local_3ren_rate":
                "local_3",

            "motor_2ren_rate":
                "motor_2",

            "motor_3ren_rate":
                "motor_3",

            "boat_2ren_rate":
                "boat_2",

            "boat_3ren_rate":
                "boat_3",
        }

        for racer in racers:
            row = {
                c: 0.0
                for c in self.feature_cols
            }

            merged = {
                **common,
                **racer,
            }

            for feature in self.feature_cols:
                src = aliases.get(
                    feature,
                    feature,
                )

                if (
                    src in merged
                    and merged[src]
                    not in (None, "")
                ):
                    row[feature] = merged[src]
                    supplied.add(feature)

            lane = as_int(
                racer.get("lane")
            )

            course = as_int(
                racer.get("actual_course")
                or racer.get("entry_course"),
                lane,
            )

            row["venue"] = "芦屋"
            row["lane"] = lane
            row["entry_course"] = course

            row["reg_no"] = as_int(
                racer.get("reg_no")
                or racer.get("player_id")
            )

            for i in range(1, 7):
                lane_col = f"is_lane_{i}"

                if lane_col in row:
                    row[lane_col] = (
                        1 if lane == i else 0
                    )

                course_col = (
                    f"is_entry_course_{i}"
                )

                if course_col in row:
                    row[course_col] = (
                        1 if course == i else 0
                    )

            if "is_inner_lane" in row:
                row["is_inner_lane"] = int(
                    lane <= 2
                )

            if "is_mid_lane" in row:
                row["is_mid_lane"] = int(
                    3 <= lane <= 4
                )

            if "is_outer_lane" in row:
                row["is_outer_lane"] = int(
                    lane >= 5
                )

            rows.append(row)

        df = pd.DataFrame(
            rows,
            columns=self.feature_cols,
        )

        # ---------------------------------
        # 生データから一意に計算できる特徴
        # ---------------------------------

        self._build_date_features(
            df,
            race,
            supplied,
        )

        self._build_entry_features(
            df,
            supplied,
        )

        self._build_race_summary(
            df,
            supplied,
            "exhibition_time",
            "exhibition_time",
        )

        self._build_race_summary(
            df,
            supplied,
            "start_timing",
            "start_timing",
        )

        self._build_race_summary(
            df,
            supplied,
            "motor_no",
            "motor_no",
        )

        self._build_race_summary(
            df,
            supplied,
            "boat_no",
            "boat_no",
        )

        self._build_race_summary(
            df,
            supplied,
            "wind_speed_mps",
            "wind_speed",
        )

        self._build_race_summary(
            df,
            supplied,
            "wave_cm",
            "wave_cm",
        )

        self._build_exhibition_features(
            df,
            supplied,
        )

        self._build_start_features(
            df,
            supplied,
        )

        self._build_tide_aliases(
            df,
            supplied,
        )

        # ---------------------------------
        # 既存のrace-relative特徴
        # ---------------------------------

        for col in list(df.columns):
            if col.endswith(
                "_rank_in_race"
            ):
                base = col[:-13]

                if base in df:
                    df[col] = (
                        pd.to_numeric(
                            df[base],
                            errors="coerce",
                        )
                        .rank(
                            ascending=False,
                            method="average",
                        )
                    )

                    supplied.add(col)

            elif col.endswith(
                "_diff_from_mean"
            ):
                base = col[:-15]

                if base in df:
                    x = (
                        pd.to_numeric(
                            df[base],
                            errors="coerce",
                        )
                        .fillna(0)
                    )

                    df[col] = (
                        x - x.mean()
                    )

                    supplied.add(col)

            elif col.endswith(
                "_zscore"
            ):
                base = col[:-7]

                if base in df:
                    x = (
                        pd.to_numeric(
                            df[base],
                            errors="coerce",
                        )
                        .fillna(0)
                    )

                    sd = x.std(
                        ddof=0
                    )

                    df[col] = (
                        x - x.mean()
                    ) / (
                        sd
                        if sd > 1e-9
                        else 1.0
                    )

                    supplied.add(col)

        # ---------------------------------
        # categorical
        # ---------------------------------

        for c in self.cat_cols:
            if c not in df.columns:
                continue

            if c == "grade":
                # 現行モデルが grade のカテゴリとして
                # missing のみを保持しているため、
                # ここでは学習モデル互換を維持する。
                df[c] = "missing"

            elif c == "nearest_tide_type":
                values = (
                    df[c]
                    .astype(str)
                    .replace(
                        {
                            "満潮": "high",
                            "干潮": "low",
                        }
                    )
                )

                values = values.where(
                    values.isin(
                        CAT_LEVELS[c]
                    ),
                    "low",
                )

                df[c] = values

            elif c == "tide_phase":
                values = (
                    df[c]
                    .astype(str)
                )

                values = values.where(
                    values.isin(
                        CAT_LEVELS[c]
                    ),
                    "falling",
                )

                df[c] = values

            elif c == "wind_direction":
                values = (
                    df[c]
                    .astype(str)
                )

                values = values.where(
                    values.isin(
                        CAT_LEVELS[c]
                    ),
                    "無風",
                )

                df[c] = values

            elif c == "venue":
                df[c] = "芦屋"

            df[c] = pd.Categorical(
                df[c],
                categories=CAT_LEVELS[c],
            )

        # ---------------------------------
        # numeric
        # ---------------------------------

        for c in self.feature_cols:
            if c not in self.cat_cols:
                df[c] = (
                    pd.to_numeric(
                        df[c],
                        errors="coerce",
                    )
                    .fillna(0.0)
                )

        missing = [
            c
            for c in self.feature_cols
            if (
                c not in supplied
                and c
                not in {
                    "venue",
                    "lane",
                    "entry_course",
                    "reg_no",
                }
                and not c.startswith(
                    "is_"
                )
            )
        ]

        coverage = (
            1
            - len(missing)
            / max(
                1,
                len(self.feature_cols),
            )
        )

        return df, {
            "feature_count":
                len(self.feature_cols),

            "coverage":
                round(
                    coverage,
                    4,
                ),

            "missing_features":
                missing,
        }