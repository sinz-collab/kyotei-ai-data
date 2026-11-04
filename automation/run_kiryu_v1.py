#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, tempfile
from copy import deepcopy
from pathlib import Path
from kiryu_v1_engine import KiryuV1

def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write("\n")
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--date",required=True); ap.add_argument("--stage",choices=("morning","live"),default="morning"); ap.add_argument("--race",type=int); ap.add_argument("--data-root",default="data"); ap.add_argument("--live-root")
    a=ap.parse_args(); compact=a.date.replace("-",""); dated=Path(a.data_root)/"venues"/"kiryu"/(compact+".json")
    payload=json.loads(dated.read_text(encoding="utf-8")); engine=KiryuV1(); preds=payload.get("preds") if isinstance(payload.get("preds"),dict) else {}
    races=payload.get("races") or []
    for race in races:
        rn=int(race.get("race") or 0)
        if a.race and rn!=a.race: continue
        if a.stage=="live":
            if not a.live_root: raise RuntimeError("--live-root is required for live")
            live=Path(a.live_root); direct=json.loads((live/"direct.json").read_text(encoding="utf-8")); exhibit=json.loads((live/"exhibition.json").read_text(encoding="utf-8"))
            if any(x.get("complete") is not True or x.get("status")!="complete" for x in (direct,exhibit)): raise RuntimeError("incomplete Kiryu LIVE inputs")
            course={int(x["lane"]):int(x.get("exhibition_course") or x["lane"]) for x in exhibit.get("data",{}).get("entries",[])}
            for r in race.get("racers") or []:
                lane=int(r["lane"]); r["entry_course"]=course.get(lane,lane); r["actual_course"]=course.get(lane,lane)
        before=deepcopy(race.get("prediction"))
        pred=engine.predict(race,a.date,a.stage)
        if before and not race.get("predictionPre"): race["predictionPre"]=before
        if a.stage=="morning": race["predictionPre"]=deepcopy(pred)
        else: race["predictionFinal"]=deepcopy(pred)
        race["prediction"]=pred; preds[str(rn)]=pred
    if a.stage=="morning" and set(preds)!=set(map(str,range(1,13))): raise RuntimeError("Kiryu morning prediction requires all 12 races")
    payload["preds"]=preds; payload["engine"]="kiryu_v1"; payload["modelVersion"]="kiryu_v1_20261104"
    if set(preds)==set(map(str,range(1,13))):
        payload["predictionAvailable"]=True; payload["predictionStatus"]="available"; payload["predictionReason"]=None
    atomic(dated,payload); atomic(Path(a.data_root)/"venues"/"kiryu"/"latest.json",payload)
    print(json.dumps({"venue":"kiryu","date":a.date,"stage":a.stage,"predictions":len(preds)},ensure_ascii=False))
if __name__=="__main__": main()
