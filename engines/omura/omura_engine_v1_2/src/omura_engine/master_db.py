from __future__ import annotations
from pathlib import Path
import pandas as pd

class MasterDB:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.tables: dict[str, pd.DataFrame] = {}
        self._load()

    def _read(self, filename: str) -> pd.DataFrame:
        path = self.base_dir / filename
        if not path.exists():
            return pd.DataFrame()
        return pd.read_csv(path, encoding="utf-8-sig")

    def _load(self) -> None:
        mapping = {
            "player_course": "omura_player_course_db_v6_1.csv",
            "player_weakness": "omura_player_course_weakness_v6_1.csv",
            "entry_shift": "omura_player_entry_shift_db_v6_1.csv",
            "player_lane": "omura_player_lane_db_v6_1.csv",
            "player_st": "omura_player_st_course_db_v6_1.csv",
            "motor_course": "omura_motor_type_db_v6.csv",
            "lane_feature": "omura_lane_feature_db_v6.csv",
            "condition_scenario": "omura_condition_scenario_db_v6.csv",
        }
        for name, filename in mapping.items():
            df = self._read(filename)
            if not df.empty:
                for col in ("player_id",):
                    if col in df.columns:
                        df[col] = df[col].astype(str).str.replace(".0", "", regex=False).str.zfill(4)
                for col in ("entry_course", "lane", "motor_no"):
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
            self.tables[name] = df

    def player_course(self, player_id: str, entry_course: int) -> dict:
        df = self.tables["player_course"]
        if df.empty:
            return {}
        hit = df[(df.player_id == str(player_id).zfill(4)) & (df.entry_course == entry_course)]
        return {} if hit.empty else hit.iloc[0].to_dict()

    def player_lane(self, player_id: str, lane: int) -> dict:
        df = self.tables["player_lane"]
        if df.empty:
            return {}
        hit = df[(df.player_id == str(player_id).zfill(4)) & (df.lane == lane)]
        return {} if hit.empty else hit.iloc[0].to_dict()

    def player_st(self, player_id: str, entry_course: int) -> dict:
        df = self.tables["player_st"]
        if df.empty:
            return {}
        hit = df[(df.player_id == str(player_id).zfill(4)) & (df.entry_course == entry_course)]
        return {} if hit.empty else hit.iloc[0].to_dict()

    def motor_course(self, motor_no: int, entry_course: int) -> dict:
        df = self.tables["motor_course"]
        if df.empty:
            return {}
        hit = df[(df.motor_no == motor_no) & (df.entry_course == entry_course)]
        if hit.empty:
            return {}
        row = hit.sort_values("starts", ascending=False).iloc[0]
        return row.to_dict()
