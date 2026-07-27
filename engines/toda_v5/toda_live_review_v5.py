
from copy import deepcopy
from toda_utils_v5 import LANES, num, clamp, normalize_map
from toda_ticket_engine_v5 import build_tickets, build_upset_tickets

def _rows(doc,key):
    if not doc or doc.get("status")!="complete" or doc.get("complete") is not True:return None
    rows=(doc.get("data") or {}).get(key) or []
    return rows if len(rows)==6 else None

def _rank(rows,key):
    vals=[(int(x["lane"]),num(x.get(key),999)) for x in rows]
    vals.sort(key=lambda x:x[1])
    return {str(l):i+1 for i,(l,_) in enumerate(vals)}

def apply_live_review(prediction,documents):
    direct=_rows(documents.get("direct"),"racers")
    exhibit=_rows(documents.get("exhibition"),"entries")
    if not direct or not exhibit:return False
    baseline=prediction.setdefault("_baseline",{
        "win":deepcopy(prediction["win"]),"second":deepcopy(prediction["second"]),"third":deepcopy(prediction["third"])
    })
    exrank=_rank(exhibit,"exhibition_time")
    original=_rows(documents.get("original_exhibition"),"entries")
    orank=_rank(original,"sum") if original else {}
    dmap={str(x["lane"]):x for x in direct}; emap={str(x["lane"]):x for x in exhibit}
    wind=num((documents["direct"].get("data") or {}).get("wind_speed"),0)
    wave=num((documents["direct"].get("data") or {}).get("wave_height"),0)
    adjusted={"win":{},"second":{},"third":{}}
    review={}
    for lane in LANES:
        k=str(lane); ex=emap[k]; dr=dmap[k]
        rank_strength=3.5-exrank[k]
        course=int(num(ex.get("exhibition_course"),lane))
        course_shift=clamp(lane-course,-2,2)
        original_strength=3.5-orank[k] if k in orank else 0
        parts=-.32 if dr.get("parts_exchange") else 0
        weight=-min(.35,max(0,num(dr.get("weight_adjustment"),0))*.09)
        outer=.20 if (wind>=4 or wave>=4) and course in (3,4,5) else 0
        delta={
            "win":clamp(rank_strength*.68+course_shift*.40+original_strength*.30+parts+weight+outer,-5.5,5.5),
            "second":clamp(rank_strength*.44+course_shift*.23+original_strength*.21+parts*.5+weight*.5+outer*.7,-4,4),
            "third":clamp(rank_strength*.29+course_shift*.11+original_strength*.15+parts*.25+weight*.25+outer*.5,-3,3)
        }
        review[k]=delta
        for pos in adjusted:
            adjusted[pos][k]=num(baseline[pos][k]) + delta[pos]
    for pos in adjusted:
        prediction[pos]=normalize_map(adjusted[pos])
    prediction["probabilityReview"]={}
    for k in map(str,LANES):
        prediction["probabilityReview"][k]={
            "morningWin":baseline["win"][k],"morningSecond":baseline["second"][k],"morningThird":baseline["third"][k],
            "win":prediction["win"][k],"second":prediction["second"][k],"third":prediction["third"][k],
            "deltaWin":round(prediction["win"][k]-baseline["win"][k],1),
            "deltaSecond":round(prediction["second"][k]-baseline["second"][k],1),
            "deltaThird":round(prediction["third"][k]-baseline["third"][k],1)
        }
    prediction["probabilityReviewStatus"]="reviewed"
    prediction["probabilityFlow"].update({"realtimeApplied":True,"reviewed":True,"reviewLabel":"確率補正・再正規化済み"})
    prediction["predictionStage"]={"label":"本予想","statusText":"戸田v5：実進入・展示・オリ展示・風波反映済み","badge":"本予想","color":"green"}
    prediction["liveReviewMeta"]={"oddsUsedForProbability":False,"oddsRequiredForReview":False,"exhibitionStartUsedAlone":False,
                                  "originalExhibitionApplied":bool(original)}
    return True
