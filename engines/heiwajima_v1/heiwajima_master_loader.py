from pathlib import Path
import sqlite3, pandas as pd
class MasterLoader:
    def __init__(self,db_path=None):
        self.db_path=Path(db_path or Path(__file__).resolve().parent/'master_db'/'heiwajima_runtime_master.sqlite')
    def query(self,sql,params=()):
        con = sqlite3.connect(self.db_path)
        try:
            return pd.read_sql_query(sql, con, params=params)
        finally:
            con.close()
    def table(self,name): return self.query(f'SELECT * FROM {name}')
    def player_course(self,reg_no,course):
        return self.query('SELECT * FROM player_course_stats WHERE CAST(player_id AS TEXT)=? AND CAST(entry_course AS INTEGER)=?',(str(reg_no),int(course)))
    def player_lane(self,reg_no,lane):
        return self.query('SELECT * FROM player_lane_stats WHERE CAST(player_id AS TEXT)=? AND CAST(lane AS INTEGER)=?',(str(reg_no),int(lane)))
    def player_kimarite(self,reg_no,course=None):
        if course is None: return self.query('SELECT * FROM player_kimarite_stats WHERE CAST(reg_no AS TEXT)=?',(str(reg_no),))
        return self.query('SELECT * FROM player_course_kimarite_stats WHERE CAST(reg_no AS TEXT)=? AND CAST(course AS INTEGER)=?',(str(reg_no),int(course)))
