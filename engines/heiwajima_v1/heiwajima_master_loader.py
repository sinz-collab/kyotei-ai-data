from pathlib import Path
import sqlite3
import pandas as pd


def normalize_reg_no(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class MasterLoader:
    def __init__(self, db_path=None):
        self.db_path = Path(
            db_path
            or Path(__file__).resolve().parent
            / "master_db"
            / "heiwajima_runtime_master.sqlite"
        )

    def query(self, sql, params=()):
        with sqlite3.connect(self.db_path) as con:
            return pd.read_sql_query(sql, con, params=params)

    def table(self, name):
        return self.query(f"SELECT * FROM {name}")

    def player_course(self, reg_no, course):
        reg_no = normalize_reg_no(reg_no)
        if reg_no is None:
            return pd.DataFrame()
        return self.query(
            "SELECT * FROM player_course_stats "
            "WHERE CAST(player_id AS INTEGER)=? "
            "AND CAST(entry_course AS INTEGER)=?",
            (reg_no, int(course)),
        )

    def player_lane(self, reg_no, lane):
        reg_no = normalize_reg_no(reg_no)
        if reg_no is None:
            return pd.DataFrame()
        return self.query(
            "SELECT * FROM player_lane_stats "
            "WHERE CAST(player_id AS INTEGER)=? "
            "AND CAST(lane AS INTEGER)=?",
            (reg_no, int(lane)),
        )

    def player_kimarite(self, reg_no, course=None):
        reg_no = normalize_reg_no(reg_no)
        if reg_no is None:
            return pd.DataFrame()

        if course is None:
            return self.query(
                "SELECT * FROM player_kimarite_stats "
                "WHERE CAST(reg_no AS INTEGER)=?",
                (reg_no,),
            )

        return self.query(
            "SELECT * FROM player_course_kimarite_stats "
            "WHERE CAST(reg_no AS INTEGER)=? "
            "AND CAST(course AS INTEGER)=?",
            (reg_no, int(course)),
        )
