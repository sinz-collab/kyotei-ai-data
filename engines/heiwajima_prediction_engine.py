#!/usr/bin/env python3
from pathlib import Path
import json, math, argparse
from heiwajima_master_loader import MasterLoader
from heiwajima_water_engine import water_features
from heiwajima_scenario_engine import evaluate_scenarios
from heiwajima_ticket_engine import generate_tickets
from heiwajima_sab_engine import judge_sab

def softmax(vals):
    m=max(vals); ex=[math.exp(v-m) for v in vals]; s=sum(ex); return [x/s for x in ex]
def num(v,default=0):
    try:
        if v is None: return default
        f=float(v); return default if math.isnan(f) else f
    except: return default

def row1(df): return {} if df.empty else df.iloc[0].to_dict()

def calculate(input_data, loader=None):
    loader=loader or MasterLoader(); stage=input_data.get('stage','pre')
    actual_map={int(x['boat_no']):int(x.get('actual_course') or x['boat_no']) for x in input_data['boats']}
    entry_changed=any(k!=v for k,v in actual_map.items())
    w=water_features(input_data)
    records=[]; missing=[]
    course_base=loader.table('course_baseline')
    for b in input_data['boats']:
        boat=int(b['boat_no']); reg=str(b['reg_no']); course=actual_map[boat]
        pc=row1(loader.player_course(reg,course)); lane=row1(loader.player_lane(reg,boat)); pk=row1(loader.player_kimarite(reg,course))
        base=course_base[course_base['course'].astype(str)==str(course)]
        base=row1(base)
        if not pc: missing.append('player_course_missing')
        if not lane: missing.append('player_lane_missing')
        # log scores. Rates in source may be 0-1 or 0-100.
        def rate(d,key,default):
            x=num(d.get(key),default); return x/100 if x>1 else x
        win=max(.01,rate(base,'win_rate',[.50,.15,.12,.10,.07,.06][course-1]))
        second=max(.01,rate(base,'second_rate',[.20,.25,.18,.16,.12,.09][course-1]))
        third=max(.01,rate(base,'third_rate',[.12,.20,.21,.20,.16,.11][course-1]))
        sw,ss,st=math.log(win),math.log(second),math.log(third)
        # player-course correction
        if pc:
            rel={'A':1.0,'B':.7,'C':.4}.get(str(pc.get('reliability')),0.25)
            diff=rate(pc,'top3_vs_course_avg',0); diff=diff if abs(diff)<1 else diff/100
            sw += max(-.30,min(.30,diff*.8))*rel
            ss += max(-.25,min(.25,diff*.65))*rel
            st += max(-.22,min(.22,diff*.55))*rel
            sw += (rate(pc,'win_rate',win)-win)*.8*rel
        # lane is only supplemental after actual entry change
        if lane and not entry_changed:
            ld=rate(lane,'top3_rate',0)-rate(pc,'top3_rate',0) if pc else 0
            st += max(-.10,min(.10,ld*.25))
        # kimarite/course attack type
        if pk:
            if course==2: sw += min(.16,rate(pk,'sashi_rate_in_wins',0)*.18)
            if course in (3,4): sw += min(.18,(rate(pk,'makuri_rate_in_wins',0)+rate(pk,'makuri_zashi_rate_in_wins',0))*.12)
            if course in (5,6): st += min(.15,rate(pk,'outside_3rd_rate',0)*.16)
        # input live/season/motor corrections
        season=b.get('season') or {}; motor=b.get('motor') or {}; ex=b.get('exhibition') or {}
        sw += max(-.20,min(.20,num(season.get('form_score'),0)*.05))
        ss += max(-.18,min(.18,num(season.get('form_score'),0)*.04))
        st += max(-.16,min(.16,num(season.get('form_score'),0)*.035))
        power=num(motor.get('power_score'),0); sw+=max(-.18,min(.18,power*.04)); ss+=max(-.14,min(.14,power*.035)); st+=max(-.12,min(.12,power*.03))
        # water connection
        if course==1: sw+=w['escape_bias']
        if course in (3,4): sw+=w['center_bias']
        if course in (5,6): st+=w['outer_bias']
        # live correction; exhibition ST is intentionally tiny alone
        if stage=='final':
            sw += max(-.03,min(.03,-num(ex.get('st_delta'),0)*.15))
            straight=num(ex.get('straight_score'),0); turn=num(ex.get('turn_score'),0); summ=num(ex.get('sum_score'),0)
            sw += max(-.18,min(.18,straight*.05+turn*.04+summ*.02))
            ss += max(-.16,min(.16,turn*.05+straight*.03+summ*.02))
            st += max(-.16,min(.16,turn*.035+straight*.035+summ*.03))
        records.append({'boat_no':boat,'reg_no':reg,'lane':boat,'actual_course':course,'entry_changed':course!=boat,'win_score':sw,'second_score':ss,'third_score':st,'reason_log':[]})
    for key in ('win','second','third'):
        ps=softmax([r[f'{key}_score'] for r in records])
        for r,p in zip(records,ps): r[f'{key}_prob']=round(p,6)
    for r in records: r['top3_prob']=round(min(1,r['win_prob']+r['second_prob']+r['third_prob']),6)
    if not input_data.get('tide'): missing.append('tide_phase_unresolved')
    if stage!='final': missing+=['exhibition_pending','original_exhibition_pending']
    if any(not (b.get('motor') or {}).get('power_score') for b in input_data['boats']): missing.append('motor_data_missing')
    scenarios=evaluate_scenarios(records,w)
    tickets=generate_tickets(records,scenarios,max_tickets=int(input_data.get('max_tickets',8)))
    completeness={'master_db_loaded':True,'player_course_reflected':6-missing.count('player_course_missing'),'local_st_reflected':sum(1 for b in records if not loader.player_course(b['reg_no'],b['actual_course']).empty),'entry_changed':entry_changed,'missing_codes':sorted(set(missing))}
    sab=judge_sab(records,scenarios,completeness,len(tickets))
    # exclusion log for 10%+ win boats outside practical head set top 3
    top_heads={b['boat_no'] for b in sorted(records,key=lambda x:x['win_prob'],reverse=True)[:3]}
    exclusions=[]
    for b in records:
        if b['win_prob']>=.10 and b['boat_no'] not in top_heads: exclusions.append({'boat_no':b['boat_no'],'win_probability':b['win_prob'],'reason_codes':['scenario_priority_lower','relative_head_score']})
    return {'schema_version':'1.0.0','venue':'heiwajima','race_date':input_data['race_date'],'race_no':input_data['race_no'],'stage':stage,'entry_order':[next(x['boat_no'] for x in input_data['boats'] if actual_map[int(x['boat_no'])]==c) for c in sorted(actual_map.values())] if len(set(actual_map.values()))==6 else None,'probabilities':records,'scenarios':scenarios,'sab':sab,'tickets':tickets,'head_exclusion_log':exclusions,'data_completeness':completeness,'odds_used_for_prediction':False}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input'); ap.add_argument('-o','--output',required=True); args=ap.parse_args()
    data=json.loads(Path(args.input).read_text(encoding='utf-8')); out=calculate(data)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__': main()
