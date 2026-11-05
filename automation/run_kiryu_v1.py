#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, tempfile
from copy import deepcopy
from pathlib import Path

POSITIONS = ("win", "second", "third")

def probability_review(before, after):
    review = {}
    for lane in range(1, 7):
        key = str(lane)
        row = {}
        for position in POSITIONS:
            suffix = position[0].upper() + position[1:]
            morning = float(before[position][key])
            current = float(after[position][key])
            row[f"morning{suffix}"] = morning
            row[position] = current
            row[f"delta{suffix}"] = round(current - morning, 10)
        review[key] = row
    return review

def complete_live_document(document, name=None):
    data = document.get("data") or {}
    entries = data.get("racers") if name == "direct" else data.get("entries")
    entries = entries or []
    return (
        document.get("complete") is True
        and document.get("status") == "complete"
        and len(entries) == 6
        and {int(row.get("lane") or 0) for row in entries} == set(range(1, 7))
    )

def decorate_live_prediction(prediction, morning, documents):
    prediction["phase"] = "final"
    prediction["status"] = "complete"
    prediction["finalPredictionStatus"] = "complete"
    prediction["probabilityReview"] = probability_review(morning, prediction)
    prediction["probabilityReviewStatus"] = "reviewed"
    prediction["probabilityFlow"] = {
        "required": True,
        "baseApplied": True,
        "baseLabel": "朝予想",
        "realtimeApplied": True,
        "realtimeLabel": "展示・スリット・直前反映",
        "reviewed": True,
        "reviewLabel": "再精査後の調整数字",
        "adjustedRequired": True,
    }
    prediction["liveInput"] = {
        "complete": True,
        "required": ["direct", "exhibition", "original_exhibition"],
        "fetchedAt": {
            name: document.get("fetched_at")
            for name, document in documents.items()
        },
    }
    return prediction

def atomic(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix=".tmp")
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False,indent=2); f.write("\n")
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def main():
    from kiryu_v1_engine import KiryuV1
    from build_site_data import kiryu_v1_prediction_is_complete
    ap=argparse.ArgumentParser(); ap.add_argument("--date",required=True); ap.add_argument("--stage",choices=("morning","live"),default="morning"); ap.add_argument("--race",type=int); ap.add_argument("--data-root",default="data"); ap.add_argument("--live-root")
    a=ap.parse_args(); compact=a.date.replace("-",""); dated=Path(a.data_root)/"venues"/"kiryu"/(compact+".json")
    payload=json.loads(dated.read_text(encoding="utf-8")); engine=KiryuV1(); preds=payload.get("preds") if isinstance(payload.get("preds"),dict) else {}
    races=payload.get("races") or []
    for race in races:
        rn=int(race.get("race") or 0)
        if a.race and rn!=a.race: continue
        if a.stage=="live":
            if not a.live_root: raise RuntimeError("--live-root is required for live")
            live=Path(a.live_root)
            documents={name:json.loads((live/f"{name}.json").read_text(encoding="utf-8")) for name in ("direct","exhibition","original_exhibition")}
            direct=documents["direct"]; exhibit=documents["exhibition"]
            if any(not complete_live_document(document,name) for name,document in documents.items()): raise RuntimeError("incomplete Kiryu LIVE inputs")
            course={int(x["lane"]):int(x.get("exhibition_course") or x["lane"]) for x in exhibit.get("data",{}).get("entries",[])}
            for r in race.get("racers") or []:
                lane=int(r["lane"]); r["entry_course"]=course.get(lane,lane); r["actual_course"]=course.get(lane,lane)
        before=deepcopy(race.get("prediction"))
        morning=deepcopy(race.get("predictionPre") or before)
        pred=engine.predict(race,a.date,a.stage)
        if not kiryu_v1_prediction_is_complete(pred):
            raise RuntimeError(f"Kiryu {rn}R prediction failed the 10+10 public gate")
        if before and not race.get("predictionPre"): race["predictionPre"]=before
        if a.stage=="morning": race["predictionPre"]=deepcopy(pred)
        else:
            if not morning or morning.get("predictionStage") != "morning": raise RuntimeError("Kiryu morning prediction unavailable")
            decorate_live_prediction(pred,morning,documents)
            race["predictionFinal"]=deepcopy(pred)
        race["prediction"]=pred; preds[str(rn)]=pred
    if a.stage=="morning" and set(preds)!=set(map(str,range(1,13))): raise RuntimeError("Kiryu morning prediction requires all 12 races")
    payload["preds"]=preds; payload["engine"]="kiryu_v1"; payload["modelVersion"]="kiryu_v1_20261104"
    all_complete = set(preds)==set(map(str,range(1,13))) and all(
        kiryu_v1_prediction_is_complete(preds[str(rn)])
        for rn in range(1, 13)
    )
    if all_complete:
        payload["predictionAvailable"]=True; payload["predictionStatus"]="available"; payload["predictionReason"]=None
    else:
        payload["predictionAvailable"]=False; payload["predictionStatus"]="unavailable"; payload["predictionReason"]="kiryu_10_plus_10_gate_failed"
        raise RuntimeError("Kiryu payload failed the 12-race 10+10 public gate")
    atomic(dated,payload); atomic(Path(a.data_root)/"venues"/"kiryu"/"latest.json",payload)
    print(json.dumps({"venue":"kiryu","date":a.date,"stage":a.stage,"predictions":len(preds)},ensure_ascii=False))
if __name__=="__main__": main()
