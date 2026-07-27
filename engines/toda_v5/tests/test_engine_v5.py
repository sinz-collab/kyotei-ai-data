
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from toda_prediction_engine_v5 import TodaPredictionEngineV5

race={"racers":[
{"lane":1,"name":"塚田 修二","class":"B1","nat_win":"4.54","local_win":"3.10","avg_st":".21","motor_2":"35.6","boat_2":"41.7","season_runs":[{"finish":"3着","st":".15","course":3}]},
{"lane":2,"name":"近藤 友宝","class":"A2","nat_win":"6.0","local_win":"5.8","avg_st":".16","motor_2":"36","boat_2":"33"},
{"lane":3,"name":"関 道","class":"A2","nat_win":"5.8","local_win":"5.5","avg_st":".15","motor_2":"38","boat_2":"32"},
{"lane":4,"name":"落合 正侑","class":"B1","nat_win":"5.1","local_win":"5.0","avg_st":".17","motor_2":"37","boat_2":"32"},
{"lane":5,"name":"選手五","class":"B1","nat_win":"4.8","local_win":"4.8","avg_st":".18","motor_2":"33","boat_2":"31"},
{"lane":6,"name":"選手六","class":"B1","nat_win":"4.5","local_win":"4.5","avg_st":".19","motor_2":"31","boat_2":"30"}]}
p=TodaPredictionEngineV5().predict(race,{"tide_type":"中潮","tide_phase":"上げ","wind_speed":2,"wave_height":2})
assert round(sum(p["win"].values()),1)==100
assert round(sum(p["second"].values()),1)==100
assert round(sum(p["third"].values()),1)==100
assert p["engine"].startswith("toda_prediction_engine_v5")
assert p["ai"] and all("combo" in x for x in p["ai"])
assert p["sourceSummary"]["odds_used_for_probability"] is False
print("Toda v5 engine test passed")
