
from __future__ import annotations
from pathlib import Path
import pandas as pd
from .utils import norm_reg_no, as_int

def norm_name(v):
    return ''.join(str(v or '').replace('　',' ').split())

class PlayerDB:
    def __init__(self, data_dir: str|Path):
        p=Path(data_dir)
        self.course=self._load(p/'ashiya_player_course_db_v6_1.csv')
        self.lane=self._load(p/'ashiya_player_lane_db_v6_1.csv')
        self.shift=self._load(p/'ashiya_player_entry_shift_db_v6_1.csv')
        self.weak=self._load(p/'ashiya_player_course_weakness_v6_1.csv')
        self.summary=self._load(p/'ashiya_player_course_summary_v6_1.csv')
        self.st_course=self._load(p/'ashiya_player_st_course_db_v6_1.csv')
        self._index()
    def _load(self,path):
        df=pd.read_csv(path)
        df['reg_no']=df['player_id'].map(norm_reg_no)
        return df
    def _index(self):
        self.course_i={(r.reg_no,int(r.entry_course)):r._asdict() for r in self.course.itertuples(index=False)}
        self.lane_i={(r.reg_no,int(r.lane)):r._asdict() for r in self.lane.itertuples(index=False)}
        self.shift_i={(r.reg_no,int(r.lane),int(r.entry_course)):r._asdict() for r in self.shift.itertuples(index=False)}
        self.weak_i={(r.reg_no,int(r.entry_course)):r._asdict() for r in self.weak.itertuples(index=False)}
        self.summary_i={r.reg_no:r._asdict() for r in self.summary.itertuples(index=False)}
        self.st_course_i={(r.reg_no,int(r.entry_course)):r._asdict() for r in self.st_course.itertuples(index=False)}
        self.name_to_reg={}
        for df in (self.course,self.lane,self.summary):
            for r in df.itertuples(index=False):
                name=norm_name(getattr(r,'player_name',''))
                if name: self.name_to_reg.setdefault(name,r.reg_no)
    def resolve_reg_no(self, reg_no=None, name=None):
        key=norm_reg_no(reg_no)
        return key if key else self.name_to_reg.get(norm_name(name),'')
    def lookup(self, reg_no, lane, course, name=None):
        key=self.resolve_reg_no(reg_no,name); lane=as_int(lane); course=as_int(course,lane)
        return {
            'course':self.course_i.get((key,course)),
            'lane':self.lane_i.get((key,lane)),
            'shift':self.shift_i.get((key,lane,course)),
            'weakness':self.weak_i.get((key,course)),
            'summary':self.summary_i.get(key),
            'st_course':self.st_course_i.get((key,course)),
        }
