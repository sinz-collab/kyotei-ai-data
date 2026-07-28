#!/usr/bin/env python3
from pathlib import Path
import sqlite3, json, hashlib
import pandas as pd

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'master_source'
OUT=ROOT/'master_db'/'heiwajima_runtime_master.sqlite'

def load(name): return pd.read_csv(SOURCE/name,low_memory=False)
def clean_table(df):
    return df.dropna(axis=1,how='all').copy()
def checksum(path):
    h=hashlib.sha256();
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def main():
    if OUT.exists(): OUT.unlink()
    con=sqlite3.connect(OUT)
    tables={}
    basic=load('heiwajima_ai_basic_master_unified_v1.0.csv')
    for src,tbl in {
      'heiwajima_course_master.csv':'course_baseline',
      'heiwajima_player_master.csv':'player_local_stats',
      'heiwajima_motor_master.csv':'motor_master',
      'heiwajima_boat_master.csv':'boat_master',
      'heiwajima_st_exhibition_master.csv':'st_exhibition_rules',
      'heiwajima_trifecta_pattern_master.csv':'trifecta_pattern_master',
      'heiwajima_logic_summary.json':'logic_summary'
    }.items(): tables[tbl]=clean_table(basic[basic.source_file==src])
    surf=load('heiwajima_ai_surface_development_unified_v1.0.csv')
    for src,tbl in {
      'heiwajima_kimarite_db_v1.2.csv':'condition_kimarite',
      'heiwajima_tide_wind_wave_integrated_db_v1.2.csv':'tide_wind_wave_stats',
      'heiwajima_same_day_surface_trend_db_v1.0.csv':'same_day_surface_stats',
      'heiwajima_finish_pattern_by_condition_v1.2.csv':'finish_pattern_stats',
      'heiwajima_water_condition_db_v1.2.csv':'water_condition_stats',
      'heiwajima_tide_condition_db_v1.2.csv':'tide_condition_stats'
    }.items(): tables[tbl]=clean_table(surf[surf.source_file==src])
    kim=load('heiwajima_ai_player_kimarite_unified_v1.0.csv')
    tables['player_kimarite_stats']=clean_table(kim[kim.source_file=='heiwajima_player_kimarite_type_db_v1.0.csv'])
    tables['player_course_kimarite_stats']=clean_table(kim[kim.source_file=='heiwajima_player_course_kimarite_type_db_v1.0.csv'])
    direct={
      'heiwajima_player_course_db_v6_1.csv':'player_course_stats',
      'heiwajima_player_course_summary_v6_1.csv':'player_course_summary',
      'heiwajima_player_course_weakness_v6_1.csv':'player_course_weakness',
      'heiwajima_player_st_course_db_v6_1.csv':'player_course_st_stats',
      'heiwajima_player_lane_db_v6_1.csv':'player_lane_stats',
      'heiwajima_tide_race_db_v1_0_N.csv':'historical_tide_races',
      'heiwajima_tide_summary_v1_0_N.csv':'tide_summary',
      'heiwajima_tokyo_tide_daily_2023_2026_v1_0_N.csv':'tokyo_tide_daily',
      'heiwajima_tokyo_tide_extreme_2023_2026_v1_0_N.csv':'tokyo_tide_extremes'
    }
    for fn,tbl in direct.items(): tables[tbl]=clean_table(load(fn))
    # Runtime history table for actual entry changes. Do not fabricate historical values.
    tables['actual_entry_history']=pd.DataFrame(columns=['race_date','race_no','reg_no','lane','actual_course','entry_changed','source','recorded_at'])
    for name,df in tables.items():
        df.to_sql(name,con,index=False,if_exists='replace')
    indexes=[
      ('idx_pc','player_course_stats','player_id, entry_course'),('idx_lane','player_lane_stats','player_id, lane'),
      ('idx_pk','player_kimarite_stats','reg_no'),('idx_pck','player_course_kimarite_stats','reg_no, course'),
      ('idx_tide_date','tokyo_tide_daily','date'),('idx_actual_entry','actual_entry_history','race_date, race_no, reg_no')]
    for idx,tbl,cols in indexes:
      try: con.execute(f'CREATE INDEX {idx} ON {tbl} ({cols})')
      except sqlite3.OperationalError: pass
    con.commit()
    manifest={'schema_version':'1.0.0','tables':{k:len(v) for k,v in tables.items()},'sources':{p.name:checksum(p) for p in SOURCE.glob('*')},'entry_shift_policy':'actual_entry_reanalysis; no fabricated history'}
    (ROOT/'master_db'/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    con.close(); print(OUT)
if __name__=='__main__': main()
