import pandas as pd
#!/usr/bin/env python3
from pathlib import Path
import json, math, argparse
from heiwajima_master_loader import MasterLoader
from heiwajima_water_engine import water_features
from heiwajima_scenario_engine import evaluate_scenarios, scenario_position_adjustments
from heiwajima_ticket_engine import generate_tickets
from heiwajima_sab_engine import judge_sab
ROOT=Path(__file__).resolve().parent
CONFIG=json.loads((ROOT/"config.json").read_text(encoding="utf-8"))

def softmax(vals):
    m=max(vals); ex=[math.exp(v-m) for v in vals]; s=sum(ex) or 1; return [x/s for x in ex]
def num(v,default=0.0):
    try:
        if v is None: return default
        f=float(v); return default if math.isnan(f) else f
    except (TypeError,ValueError): return default
def row1(df): return {} if df.empty else df.iloc[0].to_dict()
def rate(d,key,default=0.0):
    x=num(d.get(key),default); return x/100 if abs(x)>1 else x
def clip(x,c): return max(-c,min(c,x))

def _day_bucket(data):
    n=int(data.get("event_day_no") or data.get("race",{}).get("event_day_no") or 1)
    final=bool(data.get("is_final_day") or data.get("race",{}).get("is_final_day"))
    if final or n>=5: return "late"
    if n>=3: return "middle"
    return "early"

def calculate(input_data, loader=None):
    loader=loader or MasterLoader(); stage=input_data.get("stage","pre")
    boats=input_data.get("boats") or input_data.get("entries") or []
    if len(boats)!=6: raise ValueError("boats/entries must contain exactly 6 boats")
    actual_map={int(b.get("boat_no") or b.get("lane")):int(b.get("actual_course") or b.get("planned_entry_course") or b.get("entry_course") or b.get("boat_no") or b.get("lane")) for b in boats}
    entry_changed=any(k!=v for k,v in actual_map.items())
    w=water_features(input_data); bucket=_day_bucket(input_data); mult=CONFIG["day_stage"][bucket]; caps=CONFIG["caps"]
    course_base=loader.table("course_baseline"); records=[]; missing=[]
    player_course_count = 0
    player_lane_count = 0
    local_st_count = 0
    for b in boats:
        boat=int(b.get("boat_no") or b.get("lane")); reg=str(b.get("reg_no") or b.get("player_id")); course=actual_map[boat]
        pc=row1(loader.player_course(reg,course)); lane=row1(loader.player_lane(reg,boat)); pk=row1(loader.player_kimarite(reg,course))
        local_df=loader.table("player_local_stats")
        local = {}
        try:
            normalized_reg = int(float(reg))
        except (TypeError, ValueError):
            normalized_reg = None

        if normalized_reg is not None and not local_df.empty:
            local_reg = pd.to_numeric(local_df["reg_no"], errors="coerce")
            local = row1(local_df[local_reg == normalized_reg])
        base=row1(course_base[course_base["course"].astype(str)==str(course)])
        if pc:
            player_course_count += 1
        else:
            missing.append("player_course_missing")

        if lane:
            player_lane_count += 1
        else:
            missing.append("player_lane_missing")

        if local:
            local_st_count += 1
        bw=max(.01,rate(base,"first_rate",[.4525,.1697,.1496,.1269,.0758,.0438][course-1]))
        bs=max(.01,rate(base,"second_rate",[.1931,.2273,.1914,.1795,.1284,.0977][course-1]))
        bt=max(.01,rate(base,"third_rate",[.1158,.1976,.1844,.1803,.1659,.1726][course-1]))
        sw,ss,st=math.log(bw),math.log(bs),math.log(bt); reasons=[]
        # 選手×コースは基礎率との差だけを使い、イン関連の二重加点を避ける
        if pc:
            rel={"A":1.0,"B":.72,"C":.45}.get(str(pc.get("reliability")),.25)
            delta=rate(pc,"win_rate",bw)-bw
            top3d=rate(pc,"top3_vs_course_avg",0); top3d=top3d if abs(top3d)<1 else top3d/100
            cw=clip(delta*.72+top3d*.16,caps["player_course_logit"])*rel
            sw+=cw; ss+=clip(top3d*.13,caps["player_course_logit"]*.65)*rel; st+=clip(top3d*.12,caps["player_course_logit"]*.55)*rel
            if abs(cw)>.015: reasons.append({"code":"player_course","delta":round(cw,4)})
        if lane and not entry_changed:
            lane_delta=rate(lane,"top3_rate",0)-(rate(pc,"top3_rate",0) if pc else 0)
            x=clip(lane_delta*.10,caps["lane_logit"]); st+=x
        if local:
            local_win=rate(local,"win_rate",0); course_neutral=.16
            x=clip((local_win-course_neutral)*.18,caps["local_logit"]); sw+=x
        # 決まり手はコース別成立筋として小さく使う
        if pk:
            if course==2: x=clip(rate(pk,"sashi_rate_in_wins",0)*.10,caps["kimarite_logit"])
            elif course in (3,4): x=clip((rate(pk,"makuri_rate_in_wins",0)+rate(pk,"makuri_zashi_rate_in_wins",0))*.065,caps["kimarite_logit"])
            else: x=0
            sw+=x
        season=b.get("season") or b.get("season_form") or {}; motor=b.get("motor") or {}; ex=b.get("exhibition") or {}
        form=num(season.get("form_score"),0); power=num(motor.get("power_score"),0)
        sx=clip(form*.055*mult["season"],caps["season_logit"]); mx=clip(power*.045*mult["motor"],caps["motor_logit"])
        sw+=sx+mx; ss+=sx*.80+mx*.78; st+=sx*.68+mx*.65
        # 水面はコースごとの展開成立にだけ接続
        wx=0
        if course==1: wx=w["escape_bias"]
        elif course in (3,4): wx=w["center_bias"]
        elif course in (5,6): st+=clip(w["outer_bias"],caps["water_logit"])
        sw+=clip(wx,caps["water_logit"]); reasons.append({"code":"water","delta":round(clip(wx,caps["water_logit"]),4)}) if abs(wx)>.01 else None
        if stage=="final":
            # 展示ST単独は最大±1.5%。直線・回り足・SUMの複合で最大±6%相当
            st_alone=clip(-num(ex.get("st_delta"),0)*.08,.015)
            straight=num(ex.get("straight_score"),0); turn=num(ex.get("turn_score"),0); summ=num(ex.get("sum_score"),0)
            composite=clip((straight*.045+turn*.040+summ*.022)*mult["live"],caps["live_logit"])
            sw+=st_alone+composite; ss+=composite*.80; st+=composite*.72
            if abs(composite)>.015: reasons.append({"code":"live_composite","delta":round(composite,4)})
        records.append({"boat_no":boat,"reg_no":reg,"lane":boat,"actual_course":course,"entry_changed":course!=boat,"win_score":sw,"second_score":ss,"third_score":st,"reason_log":reasons})
    # 一度基礎確率を作り、展開シナリオを先に評価
    for key in ("win","second","third"):
        ps=softmax([r[f"{key}_score"] for r in records])
        for r,p in zip(records,ps): r[f"{key}_prob"]=p
    scenarios=evaluate_scenarios(records,w); pos=scenario_position_adjustments(scenarios)
    # 頭候補ごとの相手連動を位置別確率へ戻す
    for r in records:
        a=pos[r["boat_no"]]; r["win_score"]+=clip((a["win"]-.10)*.55,caps["scenario_logit"]); r["second_score"]+=clip((a["second"]-.08)*.48,caps["scenario_logit"]); r["third_score"]+=clip((a["third"]-.07)*.44,caps["scenario_logit"])
    for key in ("win","second","third"):
        ps=softmax([r[f"{key}_score"] for r in records])
        for r,p in zip(records,ps): r[f"{key}_prob"]=round(p,6)
    for r in records: r["top3_prob"]=round(min(1,r["win_prob"]+r["second_prob"]+r["third_prob"]),6)
    if not input_data.get("tide"): missing.append("tide_phase_unresolved")
    if stage!="final": missing += ["exhibition_pending","original_exhibition_pending"]
    if any(not (b.get("motor") or {}).get("power_score") for b in boats): missing.append("motor_data_missing")
    tickets=generate_tickets(records,scenarios,max_tickets=int(input_data.get("max_tickets",8)))
    completeness={
        "master_db_loaded": True,
        "player_course_reflected": player_course_count,
        "player_lane_reflected": player_lane_count,
        "local_st_reflected": local_st_count,
        "entry_changed": entry_changed,
        "missing_codes": sorted(set(missing)),
    }
    sab=judge_sab(records,scenarios,completeness,len(tickets))
    top_heads={b["boat_no"] for b in sorted(records,key=lambda x:x["win_prob"],reverse=True)[:3]}
    exclusions=[{"boat_no":b["boat_no"],"win_probability":b["win_prob"],"reason_codes":["scenario_priority_lower","relative_head_score"]} for b in records if b["win_prob"]>=.10 and b["boat_no"] not in top_heads]
    return {"schema_version":"1.1.0","engine_version":CONFIG["engine_version"],"venue":"heiwajima","race_date":input_data["race_date"],"race_no":input_data["race_no"],"stage":stage,"day_stage_bucket":bucket,"entry_order":[next(x.get("boat_no",x.get("lane")) for x in boats if actual_map[int(x.get("boat_no",x.get("lane")))]==c) for c in sorted(actual_map.values())] if len(set(actual_map.values()))==6 else None,"probabilities":records,"scenarios":scenarios,"sab":sab,"tickets":tickets,"head_exclusion_log":exclusions,"data_completeness":completeness,"odds_used_for_prediction":False}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("input"); ap.add_argument("-o","--output",required=True); args=ap.parse_args()
    data=json.loads(Path(args.input).read_text(encoding="utf-8")); out=calculate(data)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
if __name__=="__main__": main()
