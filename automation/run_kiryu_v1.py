#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, tempfile
from copy import deepcopy
from pathlib import Path

POSITIONS = ("win", "second", "third")
LIVE_REQUIRED_FEATURES = (
    "actual_entry",
    "national_avg_st",
    "straight_time",
    "wind_speed",
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

def kiryu_live_documents_ready(documents):
    """Field-level gate for the two validated Kiryu LIVE corrections.

    Original exhibition can be marked partial when unused half-lap data is
    absent.  The correction still requires all six straight times and fails
    closed when any feature it actually uses is missing.
    """
    try:
        direct = documents["direct"].get("data") or {}
        exhibition = (documents["exhibition"].get("data") or {}).get("entries") or []
        original = (documents["original_exhibition"].get("data") or {}).get("entries") or []
        actual_entry = direct.get("actual_entry")
        return (
            len((direct.get("racers") or [])) == 6
            and sorted(actual_entry or []) == list(range(1, 7))
            and direct.get("wind_speed") is not None
            and len(exhibition) == 6
            and {int(row.get("lane") or 0) for row in exhibition} == set(range(1, 7))
            and all(row.get("exhibition_course") is not None for row in exhibition)
            and len(original) == 6
            and {int(row.get("lane") or 0) for row in original} == set(range(1, 7))
            and all(row.get("straight_time") is not None for row in original)
        )
    except (KeyError, TypeError, ValueError):
        return False

def build_live_calculation_input(documents, racers=None):
    """Build the canonical input that a Kiryu LIVE probability model must use."""
    if not kiryu_live_documents_ready(documents):
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
    racers_supplied = racers is not None
    racer_by_lane = {
        int(row["lane"]): row
        for row in (racers or direct.get("racers") or [])
        if row.get("lane") is not None
    }
    boats = []
    for lane in range(1, 7):
        exhibit = exhibition[lane]
        measured = original[lane]
        row = {
            "lane": lane,
            "national_avg_st": (racer_by_lane.get(lane) or {}).get("avg_st"),
            "local_avg_st": (
                (racer_by_lane.get(lane) or {}).get("boaters_local_avg_st")
                or (racer_by_lane.get(lane) or {}).get("local_st")
            ),
            "exhibition_course": exhibit.get("exhibition_course"),
            "start_time": exhibit.get("start_time"),
            "exhibition_time": exhibit.get("exhibition_time"),
            "half_lap_time": measured.get("half_lap_time"),
            "turn_time": measured.get("turn_time"),
            "straight_time": measured.get("straight_time"),
        }
        missing = [
            key for key, value in row.items()
            if key in {"exhibition_course", "straight_time"} and value is None
        ]
        if missing:
            raise RuntimeError(
                f"incomplete Kiryu LIVE features lane {lane}: {','.join(missing)}"
            )
        boats.append(row)

    if racers_supplied and not any(row["national_avg_st"] not in (None, "", "-") for row in boats):
        raise RuntimeError("incomplete Kiryu LIVE national average ST")

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
    missing_weather = ["wind_speed"] if weather.get("wind_speed") is None else []
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

def attach_live_display(race, documents):
    """Publish the fetched LIVE documents in the site-facing race domain."""
    direct = deepcopy(documents["direct"]["data"])
    exhibition = deepcopy(documents["exhibition"]["data"])
    original = deepcopy(documents["original_exhibition"]["data"])
    original_rows = {
        str(int(row["lane"])): deepcopy(row)
        for row in original.get("entries") or []
    }
    live = deepcopy(race.get("live") or {})
    live.update({
        "direct": direct,
        "weather": direct,
        "exhibition": exhibition,
        # app.js mergeOriginalExhibitionRows() requires lane-keyed rows when
        # the browser refreshes the embedded values from the LIVE documents.
        "original": original_rows,
        "original_exhibition": original,
        "originalExhibitionAvailable": bool(original_rows),
        "sourceFetchedAt": {
            name: document.get("fetched_at")
            for name, document in documents.items()
        },
    })
    race["live"] = live
    return race

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
    from kiryu_live_corrections import apply_live_corrections, normal_prediction_fields
    from build_site_data import kiryu_v1_prediction_is_complete
    ap=argparse.ArgumentParser(); ap.add_argument("--date",required=True); ap.add_argument("--stage",choices=("morning","live"),default="morning"); ap.add_argument("--race",type=int); ap.add_argument("--data-root",default="data"); ap.add_argument("--live-root")
    a=ap.parse_args(); compact=a.date.replace("-",""); dated=Path(a.data_root)/"venues"/"kiryu"/(compact+".json")
    payload=json.loads(dated.read_text(encoding="utf-8")); engine=KiryuV1(); preds=payload.get("preds") if isinstance(payload.get("preds"),dict) else {}
    races=payload.get("races") or []
    for race in races:
        rn=int(race.get("race") or 0)
        if a.race and rn!=a.race: continue
        before=deepcopy(race.get("prediction"))
        morning=deepcopy(race.get("predictionPre") or before)
        if a.stage=="live":
            if not a.live_root: raise RuntimeError("--live-root is required for live")
            live=Path(a.live_root)
            documents={name:json.loads((live/f"{name}.json").read_text(encoding="utf-8")) for name in ("direct","exhibition","original_exhibition")}
            if not kiryu_live_documents_ready(documents): raise RuntimeError("incomplete Kiryu LIVE inputs")
            if not morning or morning.get("predictionStage") != "morning": raise RuntimeError("Kiryu morning prediction unavailable")
            live_input,input_hash=build_live_calculation_input(documents,race.get("racers") or [])
            model_race=deepcopy(race)
            for racer in model_race.get("racers") or []:
                racer["entry_course"]=int(racer["lane"]); racer["actual_course"]=int(racer["lane"])
            base_joint=engine.normal_joint(model_race,a.date)
            corrected_joint,correction=apply_live_corrections(base_joint,model_race.get("racers") or [],live_input)
            pred=deepcopy(morning)
            pred.update(normal_prediction_fields(engine,corrected_joint))
            pred["predictionStage"]="live"
            pred["flyPrediction"]=deepcopy(morning["flyPrediction"])
            pred["flyPrediction"]["status"]="live"
            calculation={
                "executed":True,
                "status":"success",
                "inputHash":input_hash,
                "probabilityFeaturesUsed":list(LIVE_REQUIRED_FEATURES),
                "correction":correction,
            }
            decorate_live_prediction(pred,morning,documents,calculation)
            attach_live_display(race,documents)
        else:
            pred=engine.predict(race,a.date,a.stage)
        if not kiryu_v1_prediction_is_complete(
            pred,
            allow_legacy_frozen_fly=a.stage == "live",
        ):
            raise RuntimeError(f"Kiryu {rn}R prediction failed the 10+10 public gate")
        if before and not race.get("predictionPre"): race["predictionPre"]=before
        if a.stage=="morning": race["predictionPre"]=deepcopy(pred)
        else:
            race["predictionFinal"]=deepcopy(pred)
        race["prediction"]=pred; preds[str(rn)]=pred
    if a.stage=="morning" and set(preds)!=set(map(str,range(1,13))): raise RuntimeError("Kiryu morning prediction requires all 12 races")
    payload["preds"]=preds; payload["engine"]="kiryu_v1"; payload["modelVersion"]="kiryu_v1_20261104"
    all_complete = set(preds)==set(map(str,range(1,13))) and all(
        kiryu_v1_prediction_is_complete(
            preds[str(rn)],
            allow_legacy_frozen_fly=a.stage == "live",
        )
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
