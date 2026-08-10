
from __future__ import annotations
from pathlib import Path
import joblib, pandas as pd, numpy as np
from .utils import as_float, as_int, norm_reg_no

CAT_LEVELS={
 'venue':['芦屋'],
 'wind_direction':['北','北東','北西','南','南東','南西','東','無風','西'],
 'nearest_tide_type':['high','low'],
 'grade':['missing'],
 'tide_phase':['falling','rising'],
}

class FeatureBuilder:
    def __init__(self, model_path: str|Path):
        obj=joblib.load(model_path)
        self.feature_cols=list(obj['feature_cols']); self.cat_cols=list(obj['cat_cols'])
    def build(self, race:dict):
        racers=race.get('racers') or race.get('entries') or []
        if len(racers)!=6: raise ValueError('race requires exactly 6 racers')
        rows=[]; supplied=set()
        common={}
        common.update(race.get('weather') or {})
        common.update(race.get('tide') or {})
        common.update({k:v for k,v in race.items() if not isinstance(v,(list,dict))})
        for racer in racers:
            row={c:0.0 for c in self.feature_cols}
            merged={**common,**racer}
            aliases={
             'venue':'venue','race_date':'race_date','race_no':'race_no','lane':'lane','reg_no':'reg_no',
             'motor_no':'motor_no','boat_no':'boat_no','exhibition_time':'exhibition_time',
             'entry_course':'actual_course','start_timing':'start_timing','weather':'weather',
             'wind_direction':'wind_direction','wind_speed_mps':'wind_speed','wave_cm':'wave_height',
             'grade':'class','nearest_tide_type':'nearest_tide_type','tide_phase':'tide_phase',
             'national_win_rate':'nat_win','national_2ren_rate':'nat_2',
             'national_3ren_rate':'nat_3','local_win_rate':'local_win',
             'local_2ren_rate':'local_2','local_3ren_rate':'local_3',
             'motor_2ren_rate':'motor_2','motor_3ren_rate':'motor_3',
             'boat_2ren_rate':'boat_2','boat_3ren_rate':'boat_3',
            }
            for f in self.feature_cols:
                src=aliases.get(f,f)
                if src in merged and merged[src] not in (None,''):
                    row[f]=merged[src]; supplied.add(f)
            lane=as_int(racer.get('lane')); course=as_int(racer.get('actual_course') or racer.get('entry_course'),lane)
            row['venue']='芦屋'; row['lane']=lane; row['entry_course']=course; row['reg_no']=as_int(racer.get('reg_no') or racer.get('player_id'))
            for i in range(1,7):
                if f'is_lane_{i}' in row: row[f'is_lane_{i}']=1 if lane==i else 0
                if f'is_entry_course_{i}' in row: row[f'is_entry_course_{i}']=1 if course==i else 0
            if 'is_inner_lane' in row: row['is_inner_lane']=int(lane<=2)
            if 'is_mid_lane' in row: row['is_mid_lane']=int(3<=lane<=4)
            if 'is_outer_lane' in row: row['is_outer_lane']=int(lane>=5)
            row['grade']=str(row.get('grade') or 'missing') if str(row.get('grade') or '') in CAT_LEVELS['grade'] else 'missing'
            row['nearest_tide_type']=str(row.get('nearest_tide_type') or 'low')
            if row['nearest_tide_type'] not in CAT_LEVELS['nearest_tide_type']: row['nearest_tide_type']='low'
            row['tide_phase']=str(row.get('tide_phase') or 'falling')
            if row['tide_phase'] not in CAT_LEVELS['tide_phase']: row['tide_phase']='falling'
            row['wind_direction']=str(row.get('wind_direction') or '無風')
            if row['wind_direction'] not in CAT_LEVELS['wind_direction']: row['wind_direction']='無風'
            rows.append(row)
        df=pd.DataFrame(rows,columns=self.feature_cols)
        # Generate race-relative features where names follow training convention.
        for col in list(df.columns):
            if col.endswith('_rank_in_race'):
                base=col[:-13]
                if base in df: df[col]=pd.to_numeric(df[base],errors='coerce').rank(ascending=False,method='average')
            elif col.endswith('_diff_from_mean'):
                base=col[:-15]
                if base in df:
                    x=pd.to_numeric(df[base],errors='coerce').fillna(0); df[col]=x-x.mean()
            elif col.endswith('_zscore'):
                base=col[:-7]
                if base in df:
                    x=pd.to_numeric(df[base],errors='coerce').fillna(0); sd=x.std(ddof=0); df[col]=(x-x.mean())/(sd if sd>1e-9 else 1.0)
        for c in self.cat_cols:
            df[c]=pd.Categorical(df[c],categories=CAT_LEVELS[c])
        for c in self.feature_cols:
            if c not in self.cat_cols:
                df[c]=pd.to_numeric(df[c],errors='coerce').fillna(0.0)
        missing=[c for c in self.feature_cols if c not in supplied and c not in {'venue','lane','entry_course','reg_no'} and not c.startswith('is_') and not c.endswith(('_rank_in_race','_diff_from_mean','_zscore'))]
        coverage=1-len(missing)/max(1,len(self.feature_cols))
        return df, {'feature_count':len(self.feature_cols),'coverage':round(coverage,4),'missing_features':missing}
