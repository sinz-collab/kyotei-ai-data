from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

LANES = (1,2,3,4,5,6)
ENGINE_VERSION = 'shimonoseki_engine_v5.0-reconciled'

PATTERN_DELTAS = {
    '横一線':       {1:+0.8, 2:0.0, 3:0.0, 4:0.0, 5:0.0, 6:0.0},
    '内側先行':     {1:+4.0, 2:+2.5, 3:+1.0, 4:-0.5, 5:-2.5, 6:-2.5},
    '1・2先行':     {1:+4.0, 2:+4.0, 3:+0.8, 4:-2.3, 5:-2.3, 6:-2.3},
    'スロー先行':   {1:+4.0, 2:+2.5, 3:+2.3, 4:-2.5, 5:-2.5, 6:-2.5},
    'カベなし':     {1:-2.5, 2:-4.0, 3:+4.0, 4:+2.5, 5:+0.8, 6:+0.8},
    '2・3が遅れる': {1:-1.0, 2:-4.0, 3:-4.0, 4:+4.5, 5:+2.5, 6:+2.5},
    '中凹み':       {1:+1.8, 2:-3.2, 3:+0.7, 4:-3.0, 5:+3.8, 6:+1.4},
    '3の先攻め':    {1:-2.5, 2:-3.5, 3:+4.5, 4:+1.8, 5:+0.7, 6:-1.0},
    '中ぶくれ':     {1:-4.0, 2:-2.5, 3:+2.5, 4:+4.0, 5:+0.5, 6:-0.5},
    '外側先行':     {1:-4.0, 2:-4.0, 3:+3.5, 4:+4.0, 5:+2.5, 6:+2.0},
    'ダッシュ先行': {1:-4.0, 2:-4.0, 3:-2.5, 4:+4.5, 5:+4.0, 6:+2.5},
    'センター先行': {1:-4.0, 2:-2.5, 3:+2.5, 4:+4.0, 5:+0.5, 6:-0.5},
}


def f(v: Any, default: float=0.0) -> float:
    try:
        if v in (None,'','-'): return default
        return float(v)
    except Exception:
        return default


def normalize(d: Mapping[int,float]) -> dict[int,float]:
    x={k:max(.1,float(v)) for k,v in d.items()}
    s=sum(x.values()) or 1.0
    return {k:v/s*100.0 for k,v in x.items()}


def parse_hm(s: str) -> int:
    h,m=map(int,s.split(':'))
    return h*60+m


def baseline_maps(pre: Mapping[str,Any]) -> dict[str,dict[int,float]]:
    # accepted forms: reference rows list; site probability maps; predictionPre
    if 'probs' in pre and isinstance(pre['probs'], list):
        return {pos:{int(r['lane']):float(r[pos]) for r in pre['probs']} for pos in ('first','second','third')}
    p=pre.get('probabilities') or pre
    aliases={'first':('first','win'),'second':('second',),'third':('third',)}
    out={}
    for pos,keys in aliases.items():
        src=None
        for k in keys:
            if isinstance(p,Mapping) and isinstance(p.get(k),Mapping): src=p[k]; break
        if src is None: raise ValueError(f'missing {pos} baseline')
        out[pos]={int(k):float(v) for k,v in src.items()}
    return out


class LocalCourseST:
    def __init__(self,csv_path: str|Path):
        self.rows={}
        with open(csv_path,encoding='utf-8-sig',newline='') as fh:
            for r in csv.DictReader(fh):
                pid=str(r.get('player_id') or r.get('reg_no') or '').strip()
                course=int(float(r.get('entry_course') or 0))
                if pid and course:
                    self.rows[(pid,course)] = r
    def get(self,pid:Any,course:int,fallback:.0=0.18):
        r=self.rows.get((str(pid).strip(),int(course)))
        if not r: return {'avg_st':float(fallback),'starts':0}
        return {'avg_st':f(r.get('avg_st'),fallback),'starts':int(f(r.get('starts'),0))}


def classify_pattern(st: Mapping[int,float]) -> tuple[str,float,dict[str,float]]:
    vals=[st[l] for l in LANES]
    # lower ST = further ahead
    inner=(st[1]+st[2])/2
    center=(st[3]+st[4])/2
    dash=(st[4]+st[5]+st[6])/3
    slow=(st[1]+st[2]+st[3])/3
    field=sum(vals)/6

    # strongest explicit structures first
    if st[3] <= min(st[1],st[2],st[4],st[5],st[6])-.025:
        strength=1.0
        return '3の先攻め',strength,{'field':field,'inner':inner,'center':center}
    if st[2]-field >= .025 and st[4]-field >= .025 and st[5] <= field-.025:
        return '中凹み',min(1.0,max(.5,(max(st[2],st[4])-st[5])/.09)),{'field':field,'inner':inner,'center':center}
    if st[2]-st[3] >= .035 and st[3] <= field:
        return 'カベなし',min(1.0,max(.5,(st[2]-st[3])/.08)),{'field':field,'inner':inner,'center':center}
    if st[2]-field>=.025 and st[3]-field>=.025 and st[4] <= field-.02:
        return '2・3が遅れる',min(1.0,max(.55,(max(st[2],st[3])-st[4])/.08)),{'field':field,'inner':inner,'center':center}
    if dash <= slow-.035:
        return 'ダッシュ先行',min(1.0,max(.55,(slow-dash)/.07)),{'field':field,'inner':inner,'center':center}
    if center <= inner-.025:
        # same numerical delta table; retain the less-extreme center label
        strength=min(1.0,max(.45,(inner-center-.01)/.06))
        return 'センター先行',strength,{'field':field,'inner':inner,'center':center}
    if max(st[1],st[2],st[3])-min(st[4],st[5],st[6])>=.035:
        return '外側先行',min(1.0,max(.5,(slow-dash)/.08)),{'field':field,'inner':inner,'center':center}
    if max(st[4],st[5],st[6])-min(st[1],st[2],st[3])>=.035:
        return 'スロー先行',min(1.0,max(.5,(dash-slow)/.08)),{'field':field,'inner':inner,'center':center}
    if st[1] <= field-.02 and st[2] <= field-.02:
        return '1・2先行',.75,{'field':field,'inner':inner,'center':center}
    if max(st[1],st[2],st[3]) <= field+.015:
        return '内側先行',.65,{'field':field,'inner':inner,'center':center}
    return '横一線',.5,{'field':field,'inner':inner,'center':center}


def asymmetric_gate(ex_st:float, local_avg:float, local_starts:int, field_ex:float, field_local:float) -> tuple[float,str]:
    ex_adv=field_ex-ex_st
    loc_adv=field_local-local_avg
    sample=min(1.0,local_starts/10.0)
    if ex_adv >= .01:
        gate=.88+.10*sample
        label='展示前出しを基本信用'
        if loc_adv>=.01: gate=min(1.0,gate+.04)
        elif loc_adv<=-.02: gate=max(.82,gate-.05)
    elif ex_adv <= -.01:
        if loc_adv<=-.01:
            gate=.90+.08*sample; label='展示遅れ＋当地も遅い'
        elif loc_adv>=.01:
            gate=.35+.15*(1-sample); label='展示遅れだが当地は速い→減点緩和'
        else:
            gate=.60+.15*sample; label='展示遅れ・当地中立'
    else:
        gate=.70; label='展示中立'
    return gate,label


def precise_tide_factor(deadline:str,tide_events:list[Mapping[str,Any]],course_master:Mapping[int,Mapping[str,float]],tide_rates:Mapping[str,Mapping[int,Mapping[str,float]]]):
    # find bracketing high/low event and continuous half-cosine progress
    t=parse_hm(deadline)
    ev=[]
    for e in tide_events:
        tm=parse_hm(str(e['time'])); typ=str(e['type']); lev=f(e['level'])
        ev.append((tm,typ,lev))
    ev.sort()
    prev=None; nxt=None
    for e in ev:
        if e[0] <= t: prev=e
        elif e[0] > t and nxt is None: nxt=e
    if not prev or not nxt: return None
    if '満' in prev[1] and '干' in nxt[1]: direction='falling'
    elif '干' in prev[1] and '満' in nxt[1]: direction='rising'
    else: return None
    dur=nxt[0]-prev[0]
    if dur<=0:return None
    progress=max(0,min(1,(t-prev[0])/dur))
    # Current validated Shimonoseki rule: during the first half of a falling tide,
    # use falling_mid as the stable anchor; after 50% interpolate continuously
    # toward falling_late (then near_low). This matches the 3R/4R validation layer.
    if direction=='falling':
        if progress <= .50:
            b0=b1='falling_mid'; w=0.0
        elif progress <= .75:
            b0,b1='falling_mid','falling_late'; w=(progress-.50)/.25
        else:
            b0,b1='falling_late','near_low'; w=(progress-.75)/.25
    else:
        if progress <= .25:
            b0=b1='rising_early'; w=0.0
        elif progress <= .50:
            b0,b1='rising_early','rising_mid'; w=(progress-.25)/.25
        elif progress <= .75:
            b0,b1='rising_mid','rising_late'; w=(progress-.50)/.25
        else:
            b0,b1='rising_late','near_high'; w=(progress-.75)/.25
    out={}
    for lane in LANES:
        out[lane]={}
        for pos in ('first','second','third'):
            a=tide_rates[b0][lane][pos]; b=tide_rates[b1][lane][pos]
            empirical=(1-w)*a+w*b
            overall=course_master[lane][pos]
            ratio=max(.65,min(1.35,empirical/overall if overall else 1.0))
            out[lane][pos]=ratio**.35
    return {'progress':progress,'factors':out,'direction':direction}


def reconcile_final(pre:Mapping[str,Any], race:Mapping[str,Any], direct:Mapping[str,Any], exhibition:Mapping[str,Any], original:Mapping[str,Any], local_st_db:LocalCourseST, *, course_master=None,tide_rates=None,tide_events=None) -> dict[str,Any]:
    base=baseline_maps(pre)
    racers={int(r['lane']):r for r in race['racers']}
    dr={int(r['lane']):r for r in (direct.get('data') or {}).get('racers',[])}
    ex={int(r['lane']):r for r in (exhibition.get('data') or {}).get('entries',[])}
    og={int(r['lane']):r for r in (original.get('data') or {}).get('entries',[])}
    if set(ex)!=set(LANES): raise ValueError('incomplete exhibition')
    actual_course={l:int(ex[l].get('exhibition_course') or l) for l in LANES}
    st={l:f(ex[l].get('start_time'),.18) for l in LANES}

    # Base copies. No pre-data layer is re-run here.
    first=dict(base['first']); second=dict(base['second']); third=dict(base['third'])

    # Strong lane1 kimarite anchor, before live scenario transfer.
    r1=racers[1]
    escape=f(r1.get('boaters_escape_rate'),0)
    n=int(f(r1.get('boaters_kimarite_starts'),0))
    if escape>0 and n>0:
        w=min(.82,n/(n+5))
        first[1]=(1-w)*first[1]+w*escape

    # credible attack pressure against lane1
    pressure=0.0
    for l in (2,3,4):
        r=racers[l]
        attack=max(f(r.get('boaters_sashi_rate')),f(r.get('boaters_makuri_rate')),f(r.get('boaters_makuri_sashi_rate')))
        pressure += {2:.07,3:.08,4:.08}[l]*attack
    pressure=min(7.0,pressure)
    first[1]-=pressure

    pattern,strength,metrics=classify_pattern(st)
    entry_changed = bool((direct.get('data') or {}).get('entry_changed'))
    if entry_changed:
        # Entry-change races: actual exhibition course is used, but raw exhibition ST
        # is deliberately downweighted because approach timing is less reproducible.
        strength *= 0.70
    raw=PATTERN_DELTAS[pattern]

    local={}
    for l in LANES:
        pid=racers[l].get('player_id') or racers[l].get('reg_no')
        local[l]=local_st_db.get(pid,actual_course[l],f(racers[l].get('avg_st'),.18))
    field_ex=sum(st.values())/6
    field_local=sum(local[l]['avg_st'] for l in LANES)/6
    gates={}
    for l in LANES:
        gate,label=asymmetric_gate(st[l],local[l]['avg_st'],local[l]['starts'],field_ex,field_local)
        delta=raw[l]*strength*gate
        # 10R validation used center strength; explicit strong patterns already encoded in strength.
        first[l]+=delta; second[l]+=delta*.45; third[l]+=delta*.25
        gates[l]={'gate':gate,'label':label,'delta':delta,'local_avg_st':local[l]['avg_st'],'local_starts':local[l]['starts']}

    # SUM mean-difference; native SUM first, fallback lap+exhibition.
    sums={}
    for l in LANES:
        native=f(og.get(l,{}).get('sum'),float('nan'))
        if math.isfinite(native): sums[l]=native
        else:
            lap=f(og.get(l,{}).get('lap_time'),float('nan')); et=f(ex[l].get('exhibition_time'),float('nan'))
            if math.isfinite(lap) and math.isfinite(et): sums[l]=lap+et
    sum_diff={l:0.0 for l in LANES}
    if len(sums)==6:
        avg=sum(sums.values())/6
        for l in LANES:
            d=max(-.5,min(.5,avg-sums[l])); sum_diff[l]=d
            first[l]*=math.exp(d*.45); second[l]*=math.exp(d*.34); third[l]*=math.exp(d*.28)

    # Precise tide is part of the final validation order when provided.
    tide_meta=None
    if course_master and tide_rates and tide_events:
        tide_meta=precise_tide_factor(str(race.get('deadline')),tide_events,course_master,tide_rates)
        if tide_meta:
            for l in LANES:
                first[l]*=tide_meta['factors'][l]['first']; second[l]*=tide_meta['factors'][l]['second']; third[l]*=tide_meta['factors'][l]['third']

    first=normalize(first); second=normalize(second); third=normalize(third)

    # Final lane1 anchor: do not let live presentation overinflate lane1 beyond its own escape evidence.
    # If exhibition recess is contradicted by strong local ST, allow only a small +2pt ceiling.
    if escape>0:
        center_min=min(st[2],st[3],st[4])
        gap=max(0.0,st[1]-center_min)
        cap=escape+2.0 if gap<=.04 else escape
        if first[1]>cap:
            cut=first[1]-cap; first[1]=cap
            sw=sum(first[l] for l in (2,3,4,5,6)) or 1.0
            for l in (2,3,4,5,6): first[l]+=cut*first[l]/sw
            first=normalize(first)

    # Strong center-attack scenario transfer.
    # This is deliberately AFTER the normal live layers: when a center boat is clearly
    # ahead at the slit and has enough motor support, the scenario is no longer an
    # independent marginal event. Inner boats are compressed and the attack boat plus
    # its immediate outside follower receive the transferred probability.
    scenario_transfer=None
    if pattern in {'センター先行','中ぶくれ'}:
        center_head = 3 if st[3] < st[4] else 4
        inner_gap = st[1] - st[center_head]
        rr = racers[center_head]
        mev = rr.get('motorEvaluation') or rr.get('motor_evaluation') or {}
        mscore = f(mev.get('score'),50.0) if isinstance(mev,Mapping) else 50.0
        mrank = str(mev.get('rank') or '') if isinstance(mev,Mapping) else ''
        motor_gate = 1.0 if (mrank in {'S','A'} or mscore >= 72.0) else (0.72 if (mrank=='B' or mscore>=58.0) else 0.45)
        intensity = max(0.0,min(1.0,(inner_gap-.03)/.05))*motor_gate
        if entry_changed:
            intensity *= 0.70
        if intensity > 0:
            if center_head == 4:
                d1={1:-4.00,2:+0.50,3:-0.10,4:+3.40,5:+0.30,6:-0.10}
                d2={1:-2.00,2:-0.40,3:-0.30,4:+2.80,5:+0.10,6:-0.20}
                d3={1:-0.95,2:-0.55,3:+0.10,4:+1.73,5:+0.04,6:-0.37}
            else:
                # mirror around a 3-course attack: 4/5 are the primary outside followers.
                d1={1:-3.60,2:-0.70,3:+3.50,4:+0.55,5:+0.30,6:-0.05}
                d2={1:-1.70,2:-0.65,3:+2.45,4:+0.45,5:+0.10,6:-0.65}
                d3={1:-0.75,2:-0.55,3:+1.20,4:+0.55,5:+0.20,6:-0.65}
            for l in LANES:
                first[l]+=d1[l]*intensity; second[l]+=d2[l]*intensity; third[l]+=d3[l]*intensity
            first=normalize(first); second=normalize(second); third=normalize(third)
            scenario_transfer={'head':center_head,'inner_gap':round(inner_gap,3),'motor_score':round(mscore,2),'motor_rank':mrank,'intensity':round(intensity,4)}

    return {
        'engine':ENGINE_VERSION,
        'probabilities':{
            'win':{str(l):round(first[l],2) for l in LANES},
            'second':{str(l):round(second[l],2) for l in LANES},
            'third':{str(l):round(third[l],2) for l in LANES},
        },
        'debug':{'pattern':pattern,'pattern_strength':round(strength,4),'metrics':metrics,'escape':escape,'attack_pressure':round(pressure,3),'gates':gates,'sum_diff':sum_diff,'tide':tide_meta,'scenario_transfer':scenario_transfer,'predata_reapplied':False,'entry_changed':entry_changed}
    }


def load_course_and_tide(master_dir:str|Path):
    md=Path(master_dir)
    cm={}
    with open(md/'shimonoseki_course_remaining_master_v1.csv',encoding='utf-8-sig',newline='') as fh:
        for r in csv.DictReader(fh):
            l=int(r['lane']); cm[l]={'first':f(r['win_rate'])*100,'second':f(r['second_rate'])*100,'third':f(r['third_rate'])*100}
    rates={}
    with open(md/'shimonoseki_tide_time_course_master_v1.csv',encoding='utf-8-sig',newline='') as fh:
        for r in csv.DictReader(fh):
            b=str(r['tide_phase_bucket']); l=int(r['lane'])
            rates.setdefault(b,{})[l]={'first':f(r['win_rate'])*100,'second':f(r['second_rate'])*100,'third':f(r['third_rate'])*100}
    return cm,rates


def build_tickets(probabilities:Mapping[str,Mapping[str,float]], race:Mapping[str,Any], debug:Mapping[str,Any]) -> dict[str,list[dict[str,Any]]]:
    first={l:f(probabilities['win'][str(l)]) for l in LANES}
    second={l:f(probabilities['second'][str(l)]) for l in LANES}
    third={l:f(probabilities['third'][str(l)]) for l in LANES}
    all_t=[]
    for a in LANES:
        for b in LANES:
            if b==a: continue
            for c in LANES:
                if c in (a,b): continue
                score=first[a]/100*second[b]/100*third[c]/100
                all_t.append(((a,b,c),score))
    all_t.sort(key=lambda x:x[1],reverse=True)
    main=all_t[:6]; used={t for t,_ in main}

    racers={int(r['lane']):r for r in race['racers']}
    # deviation/cover: same first-second pair, promote a third boat when local top3 edge >=10pt and raw rank <=15.
    cover=[]
    pairs={(t[0],t[1]) for t,_ in main}
    candidates=[]
    for pair in pairs:
        pair_main=[x for x in main if (x[0][0],x[0][1])==pair]
        if not pair_main: continue
        base_third=max(pair_main,key=lambda x:x[1])[0][2]
        base_local=f(racers[base_third].get('local_3'),0)
        for rank,(t,sc) in enumerate(all_t,1):
            if (t[0],t[1])!=pair or t in used: continue
            edge=f(racers[t[2]].get('local_3'),0)-base_local
            if edge>=10 and rank<=15: candidates.append((t,sc,rank,edge))
    candidates.sort(key=lambda x:(x[3],x[1]),reverse=True)
    for x in candidates:
        if x[0] not in {z[0] for z in cover}: cover.append(x)
        if len(cover)==2: break
    if len(cover)<2:
        head=main[0][0][0]
        for rank,(t,sc) in enumerate(all_t,1):
            if t in used or any(t==z[0] for z in cover): continue
            if t[0]==head: cover.append((t,sc,rank,0.0))
            if len(cover)==2: break

    # upset: attack-head conditional scenario, not simple head replacement.
    transfer=debug.get('scenario_transfer') or {}
    attack_head=int(transfer.get('head') or 0)
    if not attack_head:
        pat=str(debug.get('pattern') or '')
        attack_head=3 if pat=='3の先攻め' else (5 if pat=='中凹み' and first[5]>5 else 0)
    upset=[]
    if attack_head in LANES:
        s2={l:second[l] for l in LANES if l!=attack_head}; s3={l:third[l] for l in LANES if l!=attack_head}
        if attack_head==4:
            m2={1:.45,2:.55,3:.80,5:3.40,6:1.65}; m3={1:.65,2:.75,3:1.35,5:1.70,6:1.45}
        elif attack_head==3:
            m2={1:.75,2:.55,4:2.50,5:1.85,6:1.30}; m3={1:1.00,2:.70,4:1.65,5:1.70,6:1.35}
        elif attack_head==5:
            m2={1:1.0,2:.55,3:.70,4:1.45,6:2.30}; m3={1:1.20,2:.75,3:.90,4:1.50,6:2.00}
        else:
            m2={l:1.0 for l in s2}; m3={l:1.0 for l in s3}
        for l,m in m2.items():
            if l in s2:s2[l]*=m
        for l,m in m3.items():
            if l in s3:s3[l]*=m
        s2=normalize(s2); s3=normalize(s3)
        ct=[]
        for b in s2:
            for c in s3:
                if b==c: continue
                ct.append(((attack_head,b,c),s2[b]/100*s3[c]/100))
        ct.sort(key=lambda x:x[1],reverse=True)
        upset=ct[:2]
    else:
        head=main[0][0][0]
        upset=[x for x in all_t if x[0][0]!=head][:2]

    return {
        'main':[{'combo':'-'.join(map(str,t)),'score':round(sc*100,4)} for t,sc in main],
        'deviation':[{'combo':'-'.join(map(str,t)),'score':round(sc*100,4),'raw_rank':rank,'local_top3_edge':round(edge,2)} for t,sc,rank,edge in cover],
        'upset':[{'combo':'-'.join(map(str,t)),'scenario_score':round(sc*100,4)} for t,sc in upset],
    }
