from pathlib import Path
from toda_utils_v5 import LANES, num, clamp, exp_softmax
from toda_master_loader_v5 import TodaMasterV5
from toda_scenario_engine_v5 import detect_scenarios
from toda_ticket_engine_v5 import build_head_conditionals, build_tickets, build_upset_tickets
from toda_sab_engine_v5 import judge_sab

ENGINE_ID="toda_prediction_engine_v5_20260809_entry_tide_fix"
MASTER_ID="Toda_AI_MASTER_v3_1_COMPLETE_ONE_FILE"

CLASS_BONUS={"A1":2.25,"A2":1.20,"B1":0.0,"B2":-1.15}
LANE_PRIOR={1:1.65,2:.50,3:.22,4:.03,5:-.28,6:-.58}

def _positive_or(value, fallback):
    """Treat zero/negative site sentinel values as missing, not literal performance zero."""
    v=num(value, fallback)
    return v if v > 0 else fallback

class TodaPredictionEngineV5:
    def __init__(self, master_dir=None):
        self.master=TodaMasterV5(master_dir)

    def _season_form(self,r):
        runs=list(r.get("season_runs") or [])[-8:]
        if not runs:return 0
        total=weight=0
        for i,x in enumerate(runs):
            w=1+i*.10
            finish=num(x.get("finish"),6); st=num(x.get("st"),.19); course=num(x.get("course"),3.5)
            total += w*((4-finish)*.52+clamp((.19-st)*9,-.75,.75)+(.08 if course<=3 else 0))
            weight += w
        return clamp(total/weight,-2.2,2.2)

    def _base_score(self,r,lane,profile):
        nat=num(r.get("nat_win"),4.5)
        # Site JSON uses 0.00 when local history is unavailable. It must be neutral,
        # otherwise no-local-history racers receive an artificial ~-3.15 score penalty.
        local=_positive_or(r.get("local_win"),nat)
        avg=num(r.get("avg_st"),.18)
        local_st=num(r.get("local_st"),9)
        course_st=num(profile.get("avg_st"),9)
        # 0.0 is also used as a no-data sentinel for new/unknown motor and boat stats.
        motor=_positive_or(r.get("motor_2"),32)
        boat=_positive_or(r.get("boat_2"),32)
        top3diff=num(profile.get("top3_vs_course_avg"),0)
        strength=str(profile.get("strength") or "")
        strength_bonus={"得意":.60,"やや得意":.30,"苦手":-.60}.get(strength,0)
        reliability={"A":1.0,"B":.75,"C":.45}.get(str(profile.get("reliability") or ""),.4)
        course_term=clamp(top3diff/20,-1.4,1.4)*reliability
        st_term=clamp((.18-avg)*10,-.9,.9)
        if local_st<1: st_term += clamp((.18-local_st)*7,-.65,.65)
        if course_st<1: st_term += clamp((.18-course_st)*7,-.65,.65)
        return CLASS_BONUS.get(str(r.get("class","")),0)+(nat-4.5)*.88+(local-4.5)*.70+(motor-32)*.042+(boat-32)*.016+st_term+course_term+strength_bonus+LANE_PRIOR[lane]+self._season_form(r)

    def predict(self,race,context=None):
        context=context or {}
        racers=list(race.get("racers") or [])
        if len(racers)!=6: raise ValueError("racers must contain six boats")
        profiles={}; source_status={}; logs=[]
        scores={}
        for r in racers:
            lane=int(r["lane"])
            course=int(r.get("actual_course") or r.get("entry_course") or lane)
            p=self.master.course_profile(r,course)
            profiles[str(lane)]=p
            scores[str(lane)]=self._base_score(r,lane,p)
            local_win_raw=num(r.get("local_win"),0)
            motor_raw=num(r.get("motor_2"),0)
            source_status[str(lane)]={
                "player_course":"reflected" if p["matched"] else "missing",
                "sources":p.get("sources",[]),
                "local_st":"reflected" if num(r.get("local_st"),9)<1 else "missing",
                "local_win":"reflected" if local_win_raw>0 else "missing_neutral",
                "season":"reflected" if r.get("season_runs") else "missing",
                "motor":"reflected" if motor_raw>0 else "missing_neutral"
            }
            logs.append({"lane":lane,"stage":"base","notes":[
                f"course_profile={p['strength'] or 'none'}",
                f"course_sources={','.join(p.get('sources',[])) or 'none'}",
                f"local_win={'neutral_missing' if local_win_raw<=0 else local_win_raw}",
                f"motor_2={'neutral_missing' if motor_raw<=0 else motor_raw}",
                f"base_score={scores[str(lane)]:.3f}"
            ]})
        scenarios,one_weak=detect_scenarios(racers,profiles,scores,context)
        win_raw=dict(scores)
        for s in scenarios: win_raw[str(s["head"])] += s["weight"]*1.28
        win=exp_softmax(win_raw,.50)
        second_by_head={}; third_by_head={}
        for h in LANES:
            s=next((x for x in scenarios if x["head"]==h),None)
            sec,thr=build_head_conditionals(h,scores,s)
            second_by_head[str(h)]=sec; third_by_head[str(h)]=thr
        temp_heads=sorted(LANES,key=lambda x:win[str(x)],reverse=True)
        provisional=temp_heads[0]
        second=second_by_head[str(provisional)]
        third=third_by_head[str(provisional)]
        sab,axis,gap=judge_sab(win,scenarios,second_by_head,third_by_head)
        second=second_by_head[str(axis)]; third=third_by_head[str(axis)]
        tickets=build_tickets(win,second_by_head,third_by_head,scenarios,sab)
        upset=build_upset_tickets(win,second_by_head,third_by_head,scenarios)
        upset_index=round(clamp(100-win["1"]+(12 if one_weak else 0),5,95),1)
        tide_profile=self.master.tide_profile(context.get("tide_type"))
        source_summary={
            "master":"reflected",
            "player_course_reflected":sum(1 for x in source_status.values() if x["player_course"]=="reflected"),
            "player_course_missing":sum(1 for x in source_status.values() if x["player_course"]=="missing"),
            "tide_summary":"reflected" if tide_profile else "missing",
            "odds_used_for_probability":False,
            "exhibition_st_used_alone":False
        }
        return {
            "engine":ENGINE_ID,"master":MASTER_ID,
            "tidePhase":context.get("tide_phase") or "",
            "tideType":context.get("tide_type") or "",
            "win":win,"second":second,"third":third,
            "secondByHead":second_by_head,"thirdByHead":third_by_head,
            "scenarios":scenarios,"oneWeak":one_weak,
            "sab":sab,"confidence":round(clamp(47+gap*2+(8 if sab=="S" else 3 if sab=="A" else 0),40,88)),
            "upsetIndex":upset_index,
            "attack":{"attackLane":next((s["head"] for s in scenarios if s["head"]!=1),axis),
                      "label":next((s["label"] for s in scenarios if s["head"]!=1),f"{axis}号艇中心")},
            "readability":{"axisLane":axis,"comment":f"主軸{axis}号艇／展開シナリオ連動"},
            "ai":tickets,"aiUpset":upset,
            "tickets":[x["combo"] for x in tickets],
            "sourceStatus":source_status,"sourceSummary":source_summary,
            "logs":logs,
            "modelInputs":{"racers":racers,"profiles":profiles,"baseScores":scores,"expectedEntry":[1,2,3,4,5,6]},
            "probabilityFlow":{"required":True,"baseApplied":True,"baseLabel":"戸田v5事前基礎予想",
                               "realtimeApplied":False,"reviewed":False,"reviewLabel":"直前情報待ち"},
            "predictionStage":{"label":"事前予想","statusText":"戸田v5：統合マスター・選手×コース・決まり手展開連動反映",
                               "badge":"事前","color":"blue"}
        }
