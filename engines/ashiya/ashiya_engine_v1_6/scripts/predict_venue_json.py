from __future__ import annotations
import argparse,json
from copy import deepcopy
from pathlib import Path
from ashiya_engine.engine import AshiyaEngine
from ashiya_engine.utils import atomic_write_json

def merge_race_payload(root:dict,race:dict)->dict:
    payload=deepcopy(race)
    payload.setdefault('date',root.get('date'))
    payload.setdefault('venue',root.get('venue') or root.get('venueName') or '芦屋')
    payload.setdefault('weather',race.get('direct') or race.get('weather') or root.get('weather') or {})
    payload.setdefault('tide',race.get('tide') or root.get('tide') or {})
    # Prefer live racer collection when present, but preserve morning stats.
    racers=payload.get('racers') or payload.get('entries') or []
    direct=(race.get('live') or {}).get('direct') or race.get('direct') or {}
    live_racers=direct.get('racers') or []
    live_by_lane={int(x.get('lane',0)):x for x in live_racers if isinstance(x,dict)}
    for r in racers:
        lane=int(r.get('lane',0)); r.update({k:v for k,v in live_by_lane.get(lane,{}).items() if v not in (None,'')})
    payload['racers']=racers
    return payload

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--input',required=True);p.add_argument('--output',required=True)
    p.add_argument('--models',required=True);p.add_argument('--player-db',required=True)
    p.add_argument('--race',type=int);p.add_argument('--stage',default='pre',choices=['pre','live','final'])
    a=p.parse_args()
    doc=json.loads(Path(a.input).read_text(encoding='utf-8'))
    races=doc.get('races') if isinstance(doc,dict) else None
    if not isinstance(races,list): races=[doc]
    engine=AshiyaEngine(a.models,a.player_db)
    results=[]
    for race in races:
        no=int(race.get('race_no') or race.get('race') or 0)
        if a.race and no!=a.race: continue
        results.append(engine.predict(merge_race_payload(doc,race),stage=a.stage))
    out={'engine':'ashiya_prediction_engine','version':'1.6.0','date':doc.get('date'),'venue':'ashiya','predictions':results}
    atomic_write_json(a.output,out)
    print(json.dumps({'output':a.output,'races':len(results)},ensure_ascii=False))
if __name__=='__main__':main()
