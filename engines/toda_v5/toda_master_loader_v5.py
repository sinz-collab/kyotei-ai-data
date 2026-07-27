
from pathlib import Path
import json
import sqlite3
from toda_utils_v5 import norm_name

class TodaMasterV5:
    def __init__(self, master_dir=None):
        base = Path(master_dir or Path(__file__).parent / "master_json")
        self.db_path = base / "toda_master_v5.sqlite3"
        if not self.db_path.exists():
            raise FileNotFoundError(f"Master DB not found: {self.db_path}")

    def _query_rows(self, sql, params):
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(sql, params).fetchall()
            return [json.loads(row[0]) for row in rows]
        finally:
            con.close()

    def player_rows(self, racer, course):
        pid = str(
            racer.get("player_id")
            or racer.get("reg_no")
            or racer.get("registration_no")
            or ""
        ).strip()
        name = norm_name(racer.get("name") or racer.get("player_name"))
        rows = []
        if pid:
            rows = self._query_rows(
                "SELECT payload FROM player_rows WHERE player_id=? AND course=?",
                (pid, str(course)),
            )
            if not rows:
                rows = self._query_rows(
                    "SELECT payload FROM player_rows WHERE player_id=?",
                    (pid,),
                )
        if not rows and name:
            rows = self._query_rows(
                "SELECT payload FROM player_rows WHERE player_name_norm=? AND course=?",
                (name, str(course)),
            )
            if not rows:
                rows = self._query_rows(
                    "SELECT payload FROM player_rows WHERE player_name_norm=?",
                    (name,),
                )
        return rows

    def course_profile(self, racer, course):
        rows = self.player_rows(racer, course)
        out = {
            "matched": bool(rows),
            "sources": sorted({r.get("_source", "") for r in rows}),
            "starts": 0,
            "win_rate": None,
            "second_rate": None,
            "third_rate": None,
            "top3_rate": None,
            "avg_st": None,
            "strength": "",
            "reliability": "",
            "entry_shift_rate": None,
            "top3_vs_course_avg": None,
        }
        if not rows:
            return out

        priority = {
            "04_PlayerCourse_FULL": 0,
            "11_EntryShift": 1,
            "12_LaneDB": 2,
            "13_STCourse": 3,
            "09_CourseSummary": 4,
            "10_CourseWeak": 5,
        }
        rows = sorted(rows, key=lambda r: priority.get(r.get("_source"), 9))
        keys = (
            "starts",
            "win_rate",
            "second_rate",
            "third_rate",
            "top3_rate",
            "avg_st",
            "course_strength",
            "reliability",
            "entry_shift_rate",
            "top3_vs_course_avg",
        )
        for key in keys:
            for row in rows:
                if key in row and row[key] not in ("", None):
                    target = "strength" if key == "course_strength" else key
                    out[target] = row[key]
                    break
        return out

    def tide_profile(self, tide_type):
        if not tide_type:
            return None
        aliases = (str(tide_type), f"{tide_type}相当")
        con = sqlite3.connect(self.db_path)
        try:
            for alias in aliases:
                row = con.execute(
                    "SELECT payload FROM tide_summary WHERE tide_type=?",
                    (alias,),
                ).fetchone()
                if row:
                    return json.loads(row[0])
        finally:
            con.close()
        return None
