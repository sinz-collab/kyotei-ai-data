#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from copy import deepcopy
from pathlib import Path

POSITIONS = ("win", "second", "third")
LIVE_REQUIRED_FEATURES = (
    "actual_entry",
    "exhibition_course",
    "start_time",
    "exhibition_time",
    "half_lap_time",
    "turn_time",
    "straight_time",
    "weather",
    "air_temperature",
    "water_temperature",
    "wind_direction",
    "wind_speed",
    "wave_height",
)

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

def build_live_calculation_input(documents):
    """Build the canonical input that a Kiryu LIVE probability model must use."""
    if any(
        not complete_live_document(document, name)
        for name, document in documents.items()
    ):
        raise RuntimeError("incomplete Kiryu LIVE inputs")

    direct = documents["direct"]["data"]
    exhibition = {
        int(row["lane"]): row
        for row in documents["exhibition"]["data"]["entries"]
    }
    original = {
        int(row["lane"]): row
        for row in documents["original_exhibition"]["data"]["entries"]
    }
    boats = []
    for lane in range(1, 7):
        exhibit = exhibition[lane]
        measured = original[lane]
        row = {
            "lane": lane,
            "exhibition_course": exhibit.get("exhibition_course"),
            "start_time": exhibit.get("start_time"),
            "exhibition_time": exhibit.get("exhibition_time"),
            "half_lap_time": measured.get("half_lap_time"),
            "turn_time": measured.get("turn_time"),
            "straight_time": measured.get("straight_time"),
        }
        missing = [key for key, value in row.items() if key != "lane" and value is None]
        if missing:
            raise RuntimeError(
                f"incomplete Kiryu LIVE features lane {lane}: {','.join(missing)}"
            )
        boats.append(row)

    weather = {
        key: direct.get(key)
        for key in (
            "weather",
            "air_temperature",
            "water_temperature",
            "wind_direction",
            "wind_speed",
            "wave_height",
        )
    }
    missing_weather = [key for key, value in weather.items() if value is None]
    if missing_weather:
        raise RuntimeError(
            "incomplete Kiryu LIVE weather: " + ",".join(missing_weather)
        )

    actual_entry = direct.get("actual_entry")
    if actual_entry != list(range(1, 7)) and sorted(actual_entry or []) != list(range(1, 7)):
        raise RuntimeError("incomplete Kiryu LIVE actual entry")

    value = {
        "schema": "kiryu_live_probability_input_v1",
        "actual_entry": actual_entry,
        "boats": boats,
        "weather": weather,
    }
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return value, hashlib.sha256(encoded).hexdigest()

def require_live_probability_calculation(live_input, input_hash):
    """Fail closed until a validated Kiryu LIVE probability model exists.

    The frozen production engine has no coefficients for exhibition ST/time,
    original exhibition, or weather. Re-running the morning model is not a
    successful final prediction.
    """
    if not live_input or not input_hash:
        raise RuntimeError("Kiryu LIVE calculation input was not built")
    raise RuntimeError(
        "Kiryu LIVE probability calculation is unavailable: "
        f"validated realtime coefficients are not implemented (input_hash={input_hash})"
    )

def decorate_live_prediction(prediction, morning, documents, calculation):
    if not calculation.get("executed") or calculation.get("status") != "success":
        raise RuntimeError("Kiryu LIVE prediction cannot be completed without calculation")
    if not calculation.get("inputHash"):
        raise RuntimeError("Kiryu LIVE prediction cannot be completed without input hash")
    if set(calculation.get("probabilityFeaturesUsed") or ()) != set(LIVE_REQUIRED_FEATURES):
        raise RuntimeError("Kiryu LIVE prediction cannot omit required probability features")
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
        "inputHash": calculation["inputHash"],
        "required": ["direct", "exhibition", "original_exhibition"],
        "fetchedAt": {
            name: document.get("fetched_at")
            for name, document in documents.items()
        },
    }
    prediction["liveCalculation"] = calculation
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
            live_input,input_hash=build_live_calculation_input(documents)
            require_live_probability_calculation(live_input,input_hash)
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
            decorate_live_prediction(pred,morning,documents,{"executed":True,"status":"success","inputHash":input_hash,"probabilityFeaturesUsed":list(LIVE_REQUIRED_FEATURES)})
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
