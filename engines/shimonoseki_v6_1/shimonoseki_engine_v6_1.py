from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from .shimonoseki_v6_1_core import LANES, ShimonosekiV61Core, f, i

ENGINE_ID = "shimonoseki_engine_v6.1"
ENGINE_VERSION = "6.1"


def _prob_valid(values: Mapping[str, Any]) -> bool:
    if set(map(str, values)) != {str(x) for x in LANES}: return False
    nums=[f(values[str(x)],-1) for x in LANES]
    return all(0<=x<=100 for x in nums) and abs(sum(nums)-100)<=.05


def _probability_review(pre: Mapping[str,Any], final: Mapping[str,Any]) -> dict[str,dict[str,float]]:
    out={}
    for lane in LANES:
        k=str(lane)
        mw=round(f((pre.get("win") or {}).get(k)),1); ms=round(f((pre.get("second") or {}).get(k)),1); mt=round(f((pre.get("third") or {}).get(k)),1)
        w=round(f((final.get("win") or {}).get(k)),1); s=round(f((final.get("second") or {}).get(k)),1); t=round(f((final.get("third") or {}).get(k)),1)
        out[k]={"morningWin":mw,"morningSecond":ms,"morningThird":mt,"win":w,"second":s,"third":t,"deltaWin":round(w-mw,1),"deltaSecond":round(s-ms,1),"deltaThird":round(t-mt,1)}
    return out


class ShimonosekiSiteEngineV61:
    def __init__(self, master_dir: str|Path): self.core=ShimonosekiV61Core(master_dir)

    def preliminary_race(self,race:MutableMapping[str,Any],tide_events=None,dynamic_motor=None):
        # Dynamic motor data is attached in the same shape as v6 when supplied by production runner.
        if dynamic_motor:
            for racer in race.get("racers") or []:
                m=str(racer.get("motor_no") or "").lstrip("0") or "0"; row=dynamic_motor.get(m)
                if row:
                    racer["motor_recent"]={"trend":"up" if f(row.get("recent_trend_delta"))>0 else ("down" if f(row.get("recent_trend_delta"))<0 else "flat"),"top2_rate":f(row.get("recent_top2_rate"))*100,"top3_rate":f(row.get("recent_top3_rate"))*100}
        actual={l:l for l in LANES}
        probs,course=self.core.base_actual_course(race,actual); probs,motor=self.core.motor_base(probs,race); probs,series=self.core.series(probs,race); probs,water=self.core.water_tide(probs,race,actual,None,tide_events)
        maps=self.core.maps(probs); debug={"actual_course":actual,"course_remap":course,"motor":motor,"series":series,"water":water,"live":{},"latent_attack":{},"escape_attack":{},"result_used":False,"odds_used":False,"calibration_mode":"identity_pending_chronological_fit"}
        tickets=self.core.build_tickets(maps,race,debug); sab,smeta=self.core.sab_score(maps,tickets,debug); debug["sab"]=smeta
        return {"engine":ENGINE_ID,"engineVersion":ENGINE_VERSION,"phase":"preliminary","status":"complete","probabilities":maps,**maps,"sab":sab,"tickets":tickets,"ai":[x["combo"] for x in tickets["main"]],"balance":[x["combo"] for x in tickets["deviation"]],"aiUpset":[x["combo"] for x in tickets["upset"]],"debug":debug}

    def final_race(self,race,prediction_pre,direct,exhibition,original,tide_events=None):
        # Populate player IDs from direct feed when morning payload lacked them.
        direct_by_lane={i(x.get("lane")):x for x in (direct.get("data") or {}).get("racers",[])}
        for racer in race.get("racers") or []:
            x=direct_by_lane.get(i(racer.get("lane")))
            if x and x.get("player_id"): racer["player_id"]=x["player_id"]
        out=self.core.predict_final(race,exhibition,original,direct,tide_events)
        out["engine"]=ENGINE_ID; out["engineVersion"]=ENGINE_VERSION
        out["predictionPre"]={k:deepcopy(prediction_pre[k]) for k in ("win","second","third")}
        out["probabilityReview"]=_probability_review(prediction_pre,out)
        out["probabilityReviewStatus"]="reviewed"
        out["probabilityFlow"]={"reviewed":True,"realtimeApplied":True,"actualCourseRemapped":True,"baseLabel":"仮予想","realtimeLabel":"展示・スリット・直前反映","reviewLabel":"本予想"}
        return out

    @staticmethod
    def legacy_pred(prediction,prediction_pre=None):
        t=prediction["tickets"]; main=[x["combo"] for x in t.get("main",[])]; dev=[x["combo"] for x in t.get("deviation",[])]; up=[x["combo"] for x in t.get("upset",[])]
        out={"win":deepcopy(prediction["win"]),"second":deepcopy(prediction["second"]),"third":deepcopy(prediction["third"]),"sab":prediction["sab"],"ai":main,"balance":dev,"aiUpset":up,"tickets":main+dev+up,"engine":ENGINE_ID,"engineVersion":ENGINE_VERSION,"predictionStage":prediction["phase"],"fallback":False}
        if prediction["phase"]=="final" and prediction_pre is not None:
            out["predictionPre"]={k:deepcopy(prediction_pre[k]) for k in ("win","second","third")}
            out["probabilityReview"]=deepcopy(prediction.get("probabilityReview") or _probability_review(prediction_pre,prediction))
            out["probabilityReviewStatus"]="reviewed"; out["probabilityFlow"]=deepcopy(prediction.get("probabilityFlow") or {"reviewed":True,"realtimeApplied":True,"actualCourseRemapped":True})
        return out

    def apply_preliminary_daily(self,payload,dynamic_motor=None):
        races=payload.get("races") or []
        if len(races)!=12: raise RuntimeError("Shimonoseki 12R payload gate failed")
        tide=list((payload.get("tide") or {}).get("events") or []); preds={}
        for race in races:
            pred=self.preliminary_race(race,tide,dynamic_motor); race["predictionPre"]=deepcopy(pred); race["prediction"]=deepcopy(pred); race.pop("predictionFinal",None); preds[str(race["race"])]=self.legacy_pred(pred)
        payload["preds"]=preds; payload["engine"]=ENGINE_ID; payload["engineVersion"]=ENGINE_VERSION; payload["predictionAvailable"]=True; payload["predictionStatus"]="ready"; return payload

    def apply_final_race(self,payload,race_no,direct,exhibition,original):
        race=next((r for r in payload.get("races") or [] if i(r.get("race"))==race_no),None)
        if race is None: raise KeyError(race_no)
        pre=race.get("predictionPre")
        if not isinstance(pre,Mapping): raise RuntimeError("predictionPre missing")
        final=self.final_race(race,pre,direct,exhibition,original,list((payload.get("tide") or {}).get("events") or []))
        race["predictionFinal"]=deepcopy(final); race["prediction"]=deepcopy(final); payload.setdefault("preds",{})[str(race_no)]=deepcopy(final); payload["engine"]=ENGINE_ID; payload["engineVersion"]=ENGINE_VERSION; return payload

    @staticmethod
    def validate_payload(payload,require_all=True):
        if payload.get("engine")!=ENGINE_ID or payload.get("engineVersion")!=ENGINE_VERSION: return False,"engine_mismatch"
        preds=payload.get("preds");
        if not isinstance(preds,Mapping): return False,"preds_missing"
        expected={str(x) for x in range(1,13)} if require_all else set(preds)
        if require_all and set(preds)!=expected:return False,"race_count_invalid"
        for key in expected:
            pred=preds.get(key)
            if not isinstance(pred,Mapping):return False,f"prediction_{key}_missing"
            for pos in ("win","second","third"):
                if not isinstance(pred.get(pos),Mapping) or not _prob_valid(pred[pos]):return False,f"prediction_{key}_{pos}_invalid"
            tickets=pred.get("tickets")
            if isinstance(tickets,Mapping):
                combos=[x.get("combo") for group in ("main","deviation","upset") for x in tickets.get(group,[]) if isinstance(x,Mapping)]
            else: combos=list(tickets or []) if isinstance(tickets,list) else []
            if len(combos)!=10 or len(set(combos))!=10:return False,f"prediction_{key}_tickets_invalid"
            if pred.get("predictionStage")=="final":
                rev=pred.get("probabilityReview")
                if not isinstance(rev,Mapping) or set(rev)!={str(x) for x in LANES}:return False,f"prediction_{key}_review_invalid"
        return True,"ok"
