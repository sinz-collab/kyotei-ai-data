from __future__ import annotations
import json, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
from shimonoseki_v6_1.shimonoseki_engine_v6_1 import ShimonosekiSiteEngineV61

NAMES={
4:[(4596,'葛原大陽'),(4171,'榎幸司'),(3305,'小野信樹'),(4196,'田中太一郎'),(4992,'廣瀬篤哉'),(4370,'山口達也')],
5:[(3905,'天野友和'),(4016,'西川新太郎'),(4557,'松尾光広'),(4272,'大場広孝'),(3257,'田頭実'),(3484,'芝田浩治')],
7:[(4822,'百武翔'),(4164,'岩永節也'),(5054,'佐々木大河'),(3949,'今出晋二'),(4973,'栗原直也'),(3576,'白水勝也')],
}
LIVE={
4:{'course':[1,2,3,4,5,6],'st':[.11,.13,.05,.00,-.06,-.12],'ex':[6.89,6.87,6.86,6.88,6.86,6.90],'lap':[38.33,38.53,38.00,38.16,38.03,38.60],'turn':[5.13,5.33,5.72,5.68,5.48,5.64],'straight':[7.54,7.56,7.49,7.53,7.54,7.73],'sum':[45.22,45.40,44.86,45.04,44.89,45.50]},
5:{'course':[1,3,4,5,2,6],'st':[.15,.25,.36,.36,.21,.19],'ex':[6.85,6.86,6.82,6.83,6.83,6.84],'lap':[37.08,38.07,38.59,37.57,37.77,37.64],'turn':[5.46,5.71,5.89,5.54,5.79,5.65],'straight':[7.42,7.53,7.54,7.50,7.52,7.54],'sum':[43.93,44.93,45.41,44.40,44.60,44.48]},
7:{'course':[1,2,3,4,5,6],'st':[.10,.24,.06,.02,.09,.00],'ex':[6.84,6.80,6.82,6.79,6.85,6.81],'lap':[36.70,37.40,38.07,37.59,37.06,37.27],'turn':[5.27,5.43,5.66,5.61,5.45,5.58],'straight':[7.36,7.52,7.46,7.56,7.45,7.48],'sum':[43.54,44.20,44.89,44.38,43.91,44.08]},
}
TARGET={4:'1-6-3',5:'1-3-6',7:'1-4-6'}

def docs(rno):
 d=LIVE[rno]
 direct={'complete':True,'status':'complete','data':{'racers':[{'lane':l,'player_id':NAMES[rno][l-1][0],'parts_exchange':[]} for l in range(1,7)],'wind_speed':2,'wind_direction':9,'wave_height':2}}
 ex={'complete':True,'status':'complete','data':{'entries':[{'lane':l,'exhibition_course':d['course'][l-1],'start_time':d['st'][l-1],'exhibition_time':d['ex'][l-1]} for l in range(1,7)]}}
 orig={'complete':True,'status':'complete','data':{'entries':[{'lane':l,'lap_time':d['lap'][l-1],'turn_time':d['turn'][l-1],'straight_time':d['straight'][l-1],'sum':d['sum'][l-1]} for l in range(1,7)]}}
 return direct,ex,orig

def race(rno):
 return {'race':rno,'racers':[{'lane':l,'player_id':pid,'name':name,'local_win':5.0,'season_runs':[]} for l,(pid,name) in enumerate(NAMES[rno],1)]}

def main():
 eng=ShimonosekiSiteEngineV61(ROOT/'master')
 report={}
 for rno in (4,5,7):
  r=race(rno); pre=eng.preliminary_race(r,[]); direct,ex,orig=docs(rno); final=eng.final_race(r,pre,direct,ex,orig,[])
  tickets=final['ai']+final['balance']+final['aiUpset']
  report[rno]={'pre_win':pre['win'],'final_win':final['win'],'sab':final['sab'],'main':final['ai'],'deviation':final['balance'],'upset':final['aiUpset'],'target':TARGET[rno],'target_in_10':TARGET[rno] in tickets,'probabilityReview':final['probabilityReview'],'debug':final['debug']}
  assert abs(sum(final['win'].values())-100)<.06
  assert abs(sum(final['second'].values())-100)<.06
  assert abs(sum(final['third'].values())-100)<.06
  assert len(tickets)==10 and len(set(tickets))==10
  assert final['debug']['result_used'] is False and final['debug']['odds_used'] is False
  assert set(final['probabilityReview'])==set('123456')
 print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
