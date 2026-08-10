
from pathlib import Path
import sqlite3,pandas as pd,argparse
p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True);a=p.parse_args()
source=Path(a.source); con=sqlite3.connect(a.output)
files={'player_course':'ashiya_player_course_db_v6_1.csv','player_lane':'ashiya_player_lane_db_v6_1.csv','player_entry_shift':'ashiya_player_entry_shift_db_v6_1.csv','player_st_course':'ashiya_player_st_course_db_v6_1.csv','player_course_weakness':'ashiya_player_course_weakness_v6_1.csv','player_course_summary':'ashiya_player_course_summary_v6_1.csv'}
for table,fn in files.items(): pd.read_csv(source/fn).to_sql(table,con,if_exists='replace',index=False)
con.close();print(a.output)
