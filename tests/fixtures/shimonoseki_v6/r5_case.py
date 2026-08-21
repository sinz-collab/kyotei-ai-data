from engines.shimonoseki_v6.shimonoseki_v6_core import ShimonosekiV6Core

RACE={
 'race':5,'deadline':'17:17','eventDay':2,'race_meta':{'day_no':2},
 'racers':[
 {'lane':1,'name':'植田 太一','player_id':4679,'avg_st':'.16','nat_win':'6.16','local_win':'6.25','season_runs':[{'st':'.06','finish':'2着'}], 'boaters_escape_rate':70.0,'motorEvaluation':{'score':52.861,'rank':'C'},'motor_recent':{'trend':'up','top2_rate':30.0,'top3_rate':40.0}},
 {'lane':2,'name':'三好 勇人','player_id':4331,'avg_st':'.16','nat_win':'6.03','local_win':'6.18','season_runs':[{'st':'.04','finish':'3着'}], 'boaters_sashi_rate':12.5,'boaters_makuri_rate':4.2,'boaters_makuri_sashi_rate':0.0,'motorEvaluation':{'score':51.798,'rank':'C'},'motor_recent':{'trend':'up','top2_rate':20.0,'top3_rate':70.0}},
 {'lane':3,'name':'本村 大','player_id':5086,'avg_st':'.20','nat_win':'3.59','local_win':'3.96','season_runs':[{'st':'.10','finish':'1着'}], 'boaters_sashi_rate':0.0,'boaters_makuri_rate':10.0,'boaters_makuri_sashi_rate':0.0,'motorEvaluation':{'score':46.756,'rank':'C'},'motor_recent':{'trend':'up','top2_rate':40.0,'top3_rate':60.0}},
 {'lane':4,'name':'渡辺 空依','player_id':5031,'avg_st':'.18','nat_win':'4.99','local_win':'4.47','season_runs':[{'st':'.23','finish':'5着'},{'st':'.10','finish':'2着'}], 'boaters_sashi_rate':0.0,'boaters_makuri_rate':5.0,'boaters_makuri_sashi_rate':0.0,'motorEvaluation':{'score':41.883,'rank':'D'},'motor_recent':{'trend':'flat','top2_rate':20.0,'top3_rate':20.0}},
 {'lane':5,'name':'沼田 大都','player_id':5127,'avg_st':'.22','nat_win':'4.49','local_win':'4.60','season_runs':[{'st':'.21','finish':'2着'},{'st':'.09','finish':'2着'}], 'boaters_sashi_rate':0.0,'boaters_makuri_rate':0.0,'boaters_makuri_sashi_rate':0.0,'motorEvaluation':{'score':44.741,'rank':'D'},'motor_recent':{'trend':'up','top2_rate':40.0,'top3_rate':40.0}},
 {'lane':6,'name':'山口 達也','player_id':4370,'avg_st':'.14','nat_win':'6.13','local_win':'7.78','season_runs':[{'st':'.05','finish':'5着'},{'st':'.17','finish':'1着'}], 'boaters_sashi_rate':6.3,'boaters_makuri_rate':0.0,'boaters_makuri_sashi_rate':0.0,'motorEvaluation':{'score':33.906,'rank':'D'},'motor_recent':{'trend':'flat','top2_rate':20.0,'top3_rate':30.0}},
 ]}
EX={'data':{'entries':[
 {'lane':1,'exhibition_course':4,'start_time':-0.10,'exhibition_time':6.86,'exhibition_rank':1},
 {'lane':2,'exhibition_course':1,'start_time':0.06,'exhibition_time':7.04,'exhibition_rank':6},
 {'lane':3,'exhibition_course':5,'start_time':-0.17,'exhibition_time':6.88,'exhibition_rank':3},
 {'lane':4,'exhibition_course':6,'start_time':-0.18,'exhibition_time':6.87,'exhibition_rank':2},
 {'lane':5,'exhibition_course':3,'start_time':0.22,'exhibition_time':6.92,'exhibition_rank':4},
 {'lane':6,'exhibition_course':2,'start_time':-0.03,'exhibition_time':6.97,'exhibition_rank':5},
]}}
OG={'data':{'entries':[
 {'lane':1,'sum':43.94,'lap_time':37.08,'straight_time':7.52,'turn_time':5.27},
 {'lane':2,'sum':44.99,'lap_time':37.95,'straight_time':7.60,'turn_time':5.55},
 {'lane':3,'sum':44.54,'lap_time':37.66,'straight_time':7.40,'turn_time':5.75},
 {'lane':4,'sum':44.60,'lap_time':37.73,'straight_time':7.45,'turn_time':5.82},
 {'lane':5,'sum':45.29,'lap_time':38.37,'straight_time':7.60,'turn_time':5.63},
 {'lane':6,'sum':44.65,'lap_time':37.68,'straight_time':7.47,'turn_time':5.52},
]}}
TARGET={
 'win':{'1':17.57,'2':46.38,'3':12.26,'4':4.69,'5':2.55,'6':16.55},
 'second':{'1':25.16,'2':14.15,'3':15.15,'4':8.51,'5':13.16,'6':23.87},
 'third':{'1':24.73,'2':12.53,'3':17.90,'4':14.41,'5':15.00,'6':15.43},
}
