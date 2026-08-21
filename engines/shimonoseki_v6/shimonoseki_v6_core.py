from __future__ import annotations

import csv, math, re
from pathlib import Path
from typing import Any, Mapping

LANES=(1,2,3,4,5,6)
REL_MULT={"A":1.0,"B":0.8,"C":0.55,"参考":0.30,"D":0.25}
FINISH_SCORE={1:1.0,2:0.6,3:0.3,4:-0.2,5:-0.6,6:-1.0}
DAY_SERIES={1:0.0,2:0.50,3:0.75,4:0.95,5:1.10}
DAY_MOTOR={1:1.0,2:0.85,3:0.70,4:0.55,5:0.45}
DAY_EXH={1:1.0,2:0.85,3:0.65,4:0.50,5:0.45}

# v6: position-specific slit table. Course keys, not frame/lane keys.
SLIT_TABLE={
    '横一線': {1:(.8,.3,.2),2:(0,0,0),3:(0,0,0),4:(0,0,0),5:(0,0,0),6:(0,0,0)},
    '内側先行': {1:(4.0,2.0,1.0),2:(2.5,2.2,1.2),3:(1.0,1.3,1.0),4:(-.5,-.2,.3),5:(-2.5,-1.5,-.8),6:(-2.5,-1.5,-.8)},
    '1・2先行': {1:(4.0,2.0,1.0),2:(4.0,3.0,1.4),3:(.8,1.0,.9),4:(-2.3,-1.5,-.5),5:(-2.3,-1.4,-.5),6:(-2.3,-1.4,-.5)},
    'スロー先行': {1:(4.0,2.2,1.2),2:(2.5,2.0,1.2),3:(2.3,1.8,1.2),4:(-2.5,-1.3,-.5),5:(-2.5,-1.3,-.5),6:(-2.5,-1.3,-.5)},
    'カベなし': {1:(-2.5,1.0,1.2),2:(-4.0,-2.0,.4),3:(4.0,3.0,1.8),4:(2.5,2.0,1.5),5:(.8,1.2,1.3),6:(.8,1.0,1.2)},
    '2・3が遅れる': {1:(-1.0,1.0,1.2),2:(-4.0,-2.5,.3),3:(-4.0,-2.3,.5),4:(4.5,3.2,1.7),5:(2.5,2.2,1.8),6:(2.5,2.0,1.8)},
    '中凹み': {1:(1.8,1.2,1.1),2:(-3.2,-1.7,.3),3:(.7,1.0,1.0),4:(-3.0,-1.8,.4),5:(3.8,2.7,1.9),6:(1.4,1.8,1.7)},
    '3の先攻め': {1:(-2.5,1.2,1.4),2:(-3.5,-2.0,.5),3:(4.5,3.2,1.8),4:(1.8,2.0,1.5),5:(.7,1.2,1.4),6:(-1.0,.3,1.1)},
    '中ぶくれ': {1:(-4.0,.8,1.3),2:(-2.5,-1.4,.4),3:(2.5,2.5,1.6),4:(4.0,3.0,1.8),5:(.5,1.2,1.3),6:(-.5,.5,1.0)},
    '外側先行': {1:(-4.0,.7,1.2),2:(-4.0,-2.0,.3),3:(3.5,2.6,1.6),4:(4.0,3.0,1.8),5:(2.5,2.2,1.8),6:(2.0,2.0,1.8)},
    'ダッシュ先行': {1:(-4.0,.7,1.2),2:(-4.0,-2.0,.3),3:(-2.5,-1.5,.5),4:(4.5,3.2,1.8),5:(4.0,3.0,2.0),6:(2.5,2.2,1.9)},
    'センター先行': {1:(-4.0,.8,1.3),2:(-2.5,-1.4,.4),3:(2.5,2.5,1.6),4:(4.0,3.0,1.8),5:(.5,1.2,1.3),6:(-.5,.5,1.0)},
}


def f(v:Any, default:float=0.0)->float:
    try:
        if v in (None,'','-'): return default
        s=str(v).strip().replace('%','')
        if s.startswith('.'): s='0'+s
        return float(s)
    except Exception: return default

def i(v:Any, default:int=0)->int:
    try:return int(float(str(v).strip()))
    except Exception:return default

def norm(d:Mapping[int,float])->dict[int,float]:
    x={int(k):max(.01,float(v)) for k,v in d.items()}; s=sum(x.values()) or 1
    return {k:v/s*100 for k,v in x.items()}

def norm_name(v:Any)->str:
    return re.sub(r'[\s\u3000]+','',str(v or '')).replace('髙','高').replace('﨑','崎')

def read_csv(p:Path):
    with p.open(encoding='utf-8-sig',newline='') as fh:return list(csv.DictReader(fh))


def parse_hm(s:str)->int:
    h,m=map(int,str(s).split(':'))
    return h*60+m

def precise_tide_factor(deadline:str,tide_events:list[Mapping[str,Any]],course_master:Mapping[int,Mapping[str,float]],tide_rates:Mapping[str,Mapping[int,Mapping[str,float]]]):
    if not deadline or not tide_events:return None
    try:t=parse_hm(deadline)
    except Exception:return None
    ev=[]
    for e in tide_events:
        try:ev.append((parse_hm(str(e.get('time'))),str(e.get('type') or ''),f(e.get('level'))))
        except Exception:continue
    ev.sort(); prev=None; nxt=None
    for e in ev:
        if e[0]<=t:prev=e
        elif e[0]>t and nxt is None:nxt=e
    if not prev or not nxt:return None
    if '満' in prev[1] and '干' in nxt[1]:direction='falling'
    elif '干' in prev[1] and '満' in nxt[1]:direction='rising'
    else:return None
    dur=nxt[0]-prev[0]
    if dur<=0:return None
    progress=max(0,min(1,(t-prev[0])/dur))
    if direction=='falling':
        if progress<=.50:b0=b1='falling_mid';w=0.0
        elif progress<=.75:b0,b1='falling_mid','falling_late';w=(progress-.50)/.25
        else:b0,b1='falling_late','near_low';w=(progress-.75)/.25
    else:
        if progress<=.25:b0=b1='rising_early';w=0.0
        elif progress<=.50:b0,b1='rising_early','rising_mid';w=(progress-.25)/.25
        elif progress<=.75:b0,b1='rising_mid','rising_late';w=(progress-.50)/.25
        else:b0,b1='rising_late','near_high';w=(progress-.75)/.25
    if b0 not in tide_rates or b1 not in tide_rates:return None
    out={}
    for c in LANES:
        out[c]={}
        for pos in ('first','second','third'):
            a=tide_rates[b0][c][pos]; b=tide_rates[b1][c][pos]
            empirical=(1-w)*a+w*b; overall=course_master[c][pos]
            ratio=max(.65,min(1.35,empirical/overall if overall else 1.0))
            out[c][pos]=ratio**.35
    return {'progress':progress,'factors':out,'direction':direction,'bucket0':b0,'bucket1':b1,'weight':w}

class V6Master:
    def __init__(self, root:str|Path):
        self.root=Path(root)
        self.course={int(r['lane']):r for r in read_csv(self.root/'shimonoseki_course_remaining_master_v1.csv')}
        self.course_rates={l:{'first':f(r.get('win_rate'))*100,'second':f(r.get('second_rate'))*100,'third':f(r.get('third_rate'))*100} for l,r in self.course.items()}
        self.tide_rates={}
        for r in read_csv(self.root/'shimonoseki_tide_time_course_master_v1.csv'):
            b=str(r.get('tide_phase_bucket') or ''); l=i(r.get('lane'))
            if b and l:
                self.tide_rates.setdefault(b,{})[l]={'first':f(r.get('win_rate'))*100,'second':f(r.get('second_rate'))*100,'third':f(r.get('third_rate'))*100}
        self.pc={}; self.pt={}; self.st={}
        for r in read_csv(self.root/'shimonoseki_player_course_db_v6_1.csv'):
            pid=str(r.get('player_id') or ''); name=norm_name(r.get('player_name')); c=i(r.get('entry_course'))
            if pid:self.pc[('id',pid,c)]=r
            if name:self.pc[('name',name,c)]=r
        for r in read_csv(self.root/'shimonoseki_player_type_master_by_lane.csv'):
            pid=str(r.get('player_id_std') or ''); name=norm_name(r.get('player_name_std')); c=i(r.get('lane_std'))
            if pid:self.pt[('id',pid,c)]=r
            if name:self.pt[('name',name,c)]=r
        for r in read_csv(self.root/'shimonoseki_player_st_course_db_v6_1.csv'):
            pid=str(r.get('player_id') or r.get('reg_no') or ''); name=norm_name(r.get('player_name') or r.get('name')); c=i(r.get('entry_course') or r.get('course'))
            if pid:self.st[('id',pid,c)]=r
            if name:self.st[('name',name,c)]=r
    def row(self, table, racer, course):
        pid=str(racer.get('player_id') or racer.get('reg_no') or '')
        if pid and ('id',pid,course) in table:return table[('id',pid,course)]
        return table.get(('name',norm_name(racer.get('name')),course))
    def pcrow(self,r,c):return self.row(self.pc,r,c)
    def ptrow(self,r,c):return self.row(self.pt,r,c)
    def strow(self,r,c):return self.row(self.st,r,c)


def classify_by_course(stc:Mapping[int,float]):
    st={c:stc[c] for c in LANES}; vals=list(st.values()); field=sum(vals)/6
    inner=(st[1]+st[2])/2; center=(st[3]+st[4])/2; dash=(st[4]+st[5]+st[6])/3; slow=(st[1]+st[2]+st[3])/3
    if st[3] <= min(st[1],st[2],st[4],st[5],st[6])-.025:return '3の先攻め',1.0
    if st[2]-field>=.025 and st[4]-field>=.025 and st[5]<=field-.025:return '中凹み',min(1,max(.5,(max(st[2],st[4])-st[5])/.09))
    if st[2]-st[3]>=.035 and st[3]<=field:return 'カベなし',min(1,max(.5,(st[2]-st[3])/.08))
    if st[2]-field>=.025 and st[3]-field>=.025 and st[4]<=field-.02:return '2・3が遅れる',min(1,max(.55,(max(st[2],st[3])-st[4])/.08))
    if dash<=slow-.035:return 'ダッシュ先行',min(1,max(.55,(slow-dash)/.07))
    if center<=inner-.025:return 'センター先行',min(1,max(.45,(inner-center-.01)/.06))
    if max(st[1],st[2],st[3])-min(st[4],st[5],st[6])>=.035:return '外側先行',min(1,max(.5,(slow-dash)/.08))
    if max(st[4],st[5],st[6])-min(st[1],st[2],st[3])>=.035:return 'スロー先行',min(1,max(.5,(dash-slow)/.08))
    if st[1]<=field-.02 and st[2]<=field-.02:return '1・2先行',.75
    if max(st[1],st[2],st[3])<=field+.015:return '内側先行',.65
    return '横一線',.5

class ShimonosekiV6Core:
    def __init__(self, master_dir:str|Path): self.m=V6Master(master_dir)

    def actual_courses(self, race, exhibition):
        ex={i(x['lane']):x for x in (exhibition.get('data') or {}).get('entries',[])}
        return {l:i(ex.get(l,{}).get('exhibition_course') or next(r for r in race['racers'] if i(r['lane'])==l).get('actual_course') or l,l) for l in LANES}

    def base_actual_course(self, race, actual):
        racers={i(r['lane']):r for r in race['racers']}
        strengths=[]; sts=[]
        for l in LANES:
            r=racers[l]; nat=f(r.get('nat_win'),float('nan')); loc=f(r.get('local_win'),float('nan'))
            strengths.append(nat if not math.isfinite(loc) or loc<=0 else .45*nat+.55*loc); sts.append(f(r.get('avg_st'),float('nan')))
        vs=[x for x in strengths if math.isfinite(x)]; vst=[x for x in sts if math.isfinite(x)]
        ms=sum(vs)/len(vs) if vs else 4.8; mst=sum(vst)/len(vst) if vst else .18
        settings={'first':('win_rate','lane_win_score',.18,-2.0,.50,1.80,14),'second':('second_rate','lane_top2_score',.10,-1.0,.55,1.65,18),'third':('third_rate','lane_top3_score',.06,-.45,.60,1.55,18)}
        raw={p:{} for p in settings}; meta={}
        for idx,l in enumerate(LANES):
            r=racers[l]; c=actual[l]; pc=self.m.pcrow(r,c); pt=self.m.ptrow(r,c); meta[l]={'actual_course':c}
            for pos,(emp_col,type_col,bs,bst,lo,hi,pn) in settings.items():
                p0=f(self.m.course[c][{'first':'win_rate','second':'second_rate','third':'third_rate'}[pos]])
                score=p0
                if pc:
                    starts=f(pc.get('starts')); rel=str(pc.get('reliability') or ''); emp=f(pc.get(emp_col))/100
                    eff=starts*REL_MULT.get(rel,.45); blend=(eff*emp+pn*p0)/(eff+pn) if eff+pn else p0
                    score*=max(lo,min(hi,blend/p0 if p0 else 1)); meta[l][pos+'_course_blend']=blend*100; meta[l][pos+'_starts']=starts; meta[l][pos+'_rel']=rel
                if pt:
                    ts=f(pt.get(type_col),float('nan')); sr=f(pt.get('sample_reliability'),0)
                    if p0>0 and math.isfinite(ts): score*=max(.60,min(1.70,ts/p0))**(.22*sr)
                if math.isfinite(strengths[idx]): score*=math.exp(bs*(strengths[idx]-ms))
                if math.isfinite(sts[idx]): score*=math.exp(bst*(sts[idx]-mst))
                raw[pos][l]=score
        return {p:norm(v) for p,v in raw.items()},meta

    def motor_base(self, probs, race):
        # existing motor evaluation plus stronger recent10 trend layer.
        out={p:dict(v) for p,v in probs.items()}; meta={}; day=i(race.get('eventDay') or (race.get('race_meta') or {}).get('day_no') or 1,1); dc=DAY_MOTOR.get(day,.45)
        beta={'first':.00855,'second':.01045,'third':.01188}
        for r in race['racers']:
            l=i(r['lane']); ev=r.get('motorEvaluation') or {}; sc=f(ev.get('score'),50); recent=r.get('motor_recent') or {}; trend=str(recent.get('trend') or 'flat'); t2=f(recent.get('top2_rate')); t3=f(recent.get('top3_rate'))
            d={'first':0.0,'second':0.0,'third':0.0}
            if trend=='up': d={'first':1.5,'second':2.0,'third':2.3}
            elif trend=='down': d={'first':-1.3,'second':-1.6,'third':-1.8}
            if t2>=50: d['first']+=.5; d['second']+=.8
            if t3>=70: d['second']+=.5; d['third']+=1.0
            if t2>=60 and trend=='up': d['first']+=.7
            for p in out:
                out[p][l]*=math.exp(beta[p]*(sc-50)); out[p][l]+=d[p]*dc
            meta[l]={'score':sc,'trend':trend,'top2':t2,'top3':t3,'trend_delta':{p:round(d[p]*dc,3) for p in d}}
        return {p:norm(v) for p,v in out.items()},meta

    def series(self, probs, race):
        out={p:dict(v) for p,v in probs.items()}; day=i(race.get('eventDay') or (race.get('race_meta') or {}).get('day_no') or 1,1); coef=DAY_SERIES.get(day,1); meta={}
        for r in race['racers']:
            l=i(r['lane']); fs=[]; ss=[]
            for run in r.get('season_runs') or []:
                m=re.match(r'(\d+)',str(run.get('finish') or ''))
                if m and int(m.group(1)) in FINISH_SCORE: fs.append(FINISH_SCORE[int(m.group(1))])
                sv=f(run.get('st'),float('nan'))
                if math.isfinite(sv):ss.append(sv)
            fscore=sum(fs)/len(fs) if fs else 0; avg=f(r.get('avg_st'),.18); sst=sum(ss)/len(ss) if ss else avg; stscore=max(-1,min(1,(avg-sst)/.10)); comp=.8*fscore+.2*stscore
            ds={'first':comp*3.5*coef,'second':comp*4.5*coef,'third':comp*5*coef}
            for p in out:out[p][l]+=ds[p]
            meta[l]={'composite':comp,'delta':ds}
        return {p:norm(v) for p,v in out.items()},meta

    def live_layers(self, probs, race, actual, exhibition, original):
        out={p:dict(v) for p,v in probs.items()}; ex={i(x['lane']):x for x in (exhibition.get('data') or {}).get('entries',[])}; og={i(x['lane']):x for x in (original.get('data') or {}).get('entries',[])}
        day=i(race.get('eventDay') or (race.get('race_meta') or {}).get('day_no') or 1,1); ec=DAY_EXH.get(day,.45)
        # exhibition rank + SUM as one bounded group
        rank_delta={1:(2,3,3),2:(1.2,2,2.2),3:(.6,1,1.2),4:(-.4,0,.2),5:(-1,-.6,-.3),6:(-1.8,-1.2,-.8)}
        sums={l:f(og.get(l,{}).get('sum'),float('nan')) for l in LANES}; avg=sum(sums.values())/6 if all(math.isfinite(x) for x in sums.values()) else None
        emeta={}
        for l in LANES:
            rd=rank_delta.get(i(ex[l].get('exhibition_rank'),6),rank_delta[6]); sd=(0,0,0); diff=0
            if avg is not None:
                diff=avg-sums[l]
                if diff>=.20:sd=(2.5,3,3)
                elif diff>=.10:sd=(1.5,2,2)
                elif diff<=-.20:sd=(-2,-1.5,-1)
                elif diff<=-.10:sd=(-1,-.7,-.5)
            vals=[]
            for j,p in enumerate(('first','second','third')):
                dd=max(-5,min(5,(rd[j]+sd[j])*ec)); out[p][l]+=dd; vals.append(dd)
            emeta[l]={'sum_diff':diff,'delta':vals}
        out={p:norm(v) for p,v in out.items()}

        # slit by actual course; entry-changed races are deliberately 0.70 strength.
        stc={}; lane_for_course={}
        for l,c in actual.items(): stc[c]=f(ex[l].get('start_time'),.18); lane_for_course[c]=l
        pattern,strength=classify_by_course(stc); ifchg=any(actual[l]!=l for l in LANES); strength*=.70 if ifchg else 1.0
        table=SLIT_TABLE[pattern]; smeta={}
        for c in LANES:
            l=lane_for_course[c]; ds=table[c]
            # local course ST asymmetric gate
            r=next(x for x in race['racers'] if i(x['lane'])==l); sr=self.m.strow(r,c); local=f((sr or {}).get('avg_st'),f(r.get('avg_st'),.18)); starts=i((sr or {}).get('starts'),0)
            field=sum(stc.values())/6; local_all=[]
            # simple robust gate:前出し重視、遅れは当地速さで緩和
            exadv=field-stc[c]; locadv=f(r.get('avg_st'),.18)-local
            if exadv>=.01:gate=.90+min(.08,starts/100)
            elif exadv<=-.01: gate=.40 if locadv>=.01 else (.90 if locadv<=-.01 else .65)
            else: gate=.70
            vals=[]
            for j,p in enumerate(('first','second','third')):
                dd=ds[j]*strength*gate; out[p][l]+=dd; vals.append(dd)
            smeta[l]={'course':c,'st':stc[c],'pattern_delta':vals,'gate':gate}
        # Pairwise residual: protect/raise a clearly faster individual boat when the group pattern
        # would otherwise hide it. Especially important after entry changes.
        pairwise={}
        for c in range(2,7):
            l=lane_for_course[c]; il=lane_for_course[c-1]; gap=stc[c-1]-stc[c]
            d=(0.0,0.0,0.0)
            if gap>=.08: d=((6.0,2.6,.8) if c==2 else (3.2,1.8,.8))
            elif gap>=.05: d=(2.2,1.2,.6)
            elif gap>=.03: d=(1.2,.7,.4)
            # For 3-6 course, a raw ST gap is not enough: require a real attack type.
            # 2-course is allowed through a sashi/nigashi attack lane.
            r=next(x for x in race['racers'] if i(x['lane'])==l); pt=self.m.ptrow(r,c) or {}
            attack=max(f(pt.get('shimo_sashi_rate')),f(pt.get('shimo_makuri_rate')),f(pt.get('shimo_makurisashi_rate')),f(r.get('boaters_sashi_rate')),f(r.get('boaters_makuri_rate')),f(r.get('boaters_makuri_sashi_rate')))
            if c>=3 and attack<=0: d=(0.0,0.0,0.0)
            if d!=(0.0,0.0,0.0):
                out['first'][l]+=d[0]; out['second'][l]+=d[1]; out['third'][l]+=d[2]
            pairwise[l]={'course':c,'inner_gap':round(gap,3),'attack_rate':attack,'delta':d}
        return {p:norm(v) for p,v in out.items()},{'exhibition':emeta,'pattern':pattern,'pattern_strength':strength,'slit':smeta,'pairwise_residual':pairwise}

    def escape_attack_sum_interaction(self, probs, race, actual, exhibition, original):
        # Main v6 change: evaluate the actual 1-course boat, not lane 1.
        out={p:dict(v) for p,v in probs.items()}; racers={i(r['lane']):r for r in race['racers']}; lane_for_course={c:l for l,c in actual.items()}; inner=lane_for_course[1]; inner_r=racers[inner]
        pt1=self.m.ptrow(inner_r,1) or {}; escape=f(pt1.get('shimo_escape_rate') or pt1.get('all_escape_rate'),0)
        # Entry-change source rows can lack 1-course kimarite. Fall back to the player's
        # actual 1-course win evidence instead of treating missing=0% escape.
        if escape<=0:
            pc1=self.m.pcrow(inner_r,1) or {}
            escape=f(pc1.get('win_rate'),55.0)
        vuln={'sashi':f(pt1.get('all_sashare_rate'),0),'makuri':f(pt1.get('all_makurare_rate'),0),'makuri_sashi':f(pt1.get('all_makurare_zashi_rate'),0)}
        # by-lane master lacks 被攻め fields; low escape is the weakness score, attacker type provides direction.
        weakness=max(0,min(1,(55-escape)/35))
        ex={i(x['lane']):x for x in (exhibition.get('data') or {}).get('entries',[])}; og={i(x['lane']):x for x in (original.get('data') or {}).get('entries',[])}
        sums={l:f(og.get(l,{}).get('sum'),float('nan')) for l in LANES}; avg=sum(sums.values())/6 if all(math.isfinite(v) for v in sums.values()) else 0
        # select best actual-course attacker 2-5 by attack type × slit × SUM support.
        candidates=[]
        for c in (2,3,4,5):
            l=lane_for_course[c]; rr=racers[l]; pt=self.m.ptrow(rr,c) or {}
            attack=max(f(pt.get('shimo_sashi_rate')),f(pt.get('shimo_makuri_rate')),f(pt.get('shimo_makurisashi_rate')),f(pt.get('all_sashi_rate')),f(pt.get('all_makuri_rate')),f(pt.get('all_makurisashi_rate')),f(rr.get('boaters_sashi_rate')),f(rr.get('boaters_makuri_rate')),f(rr.get('boaters_makuri_sashi_rate')))
            attack_strength=max(0,min(1,attack/20))
            inner_st=f(ex[inner].get('start_time'),.18); ast=f(ex[l].get('start_time'),.18); slit=max(0,min(1,(inner_st-ast+.01)/.08))
            sd=(avg-sums[l]) if avg and math.isfinite(sums[l]) else 0; sum_support=max(0,min(1,(sd+.10)/.30))
            score=weakness*attack_strength*slit*sum_support
            candidates.append((score,l,c,attack,slit,sd))
        score,head,hc,attack,slit,sd=max(candidates)
        # residual transfer only. Maximum 8pt in strong 4-way alignment, 10 exceptional not used here.
        move=min(8.0,8.0*score)
        if move>0:
            out['first'][inner]=max(.01,out['first'][inner]-move); out['first'][head]+=move
            # linked 2/3 position residual to attacker and immediate outside boat
            outside=lane_for_course.get(min(6,hc+1));
            out['second'][head]+=move*.35; out['third'][head]+=move*.18
            if outside and outside!=head:
                out['second'][outside]+=move*.18; out['third'][outside]+=move*.28
        return {p:norm(v) for p,v in out.items()},{'inner_lane':inner,'escape':escape,'weakness':weakness,'attack_lane':head,'attack_course':hc,'attack_rate':attack,'slit_support':slit,'sum_diff':sd,'score':score,'first_transfer':move}

    def compound_attack(self, probs, race, actual, exhibition, original):
        # Residual interaction after the individual motor/series/slit/SUM layers.
        out={p:dict(v) for p,v in probs.items()}; racers={i(r['lane']):r for r in race['racers']}; ex={i(x['lane']):x for x in (exhibition.get('data') or {}).get('entries',[])}; og={i(x['lane']):x for x in (original.get('data') or {}).get('entries',[])}
        lane_for_course={c:l for l,c in actual.items()}; sums={l:f(og.get(l,{}).get('sum'),float('nan')) for l in LANES}; avg=sum(sums.values())/6 if all(math.isfinite(x) for x in sums.values()) else None
        meta={}; residual={3:(.8,.5,0),4:(1.5,.8,.3),5:(2.5,1.0,.5)}
        for c in (3,4,5):
            l=lane_for_course[c]; r=racers[l]; count=0; reasons=[]
            mr=r.get('motor_recent') or {}
            if str(mr.get('trend'))=='up':count+=1;reasons.append('motor_up')
            runs=r.get('season_runs') or []
            if any(re.match(r'[123]',str(x.get('finish') or '')) for x in runs):count+=1;reasons.append('series_top3')
            # ahead of immediate inner by >= .03
            il=lane_for_course[c-1]
            if f(ex[il].get('start_time'),.18)-f(ex[l].get('start_time'),.18)>=.03:count+=1;reasons.append('slit_inner+.03')
            if avg is not None and avg-sums[l]>=-.10:count+=1;reasons.append('sum_ok')
            pt=self.m.ptrow(r,c) or {}; attack=max(f(pt.get('shimo_sashi_rate')),f(pt.get('shimo_makuri_rate')),f(pt.get('shimo_makurisashi_rate')),f(pt.get('all_sashi_rate')),f(pt.get('all_makuri_rate')),f(pt.get('all_makurisashi_rate')),f(r.get('boaters_sashi_rate')),f(r.get('boaters_makuri_rate')),f(r.get('boaters_makuri_sashi_rate')))
            if attack>0:count+=1;reasons.append('attack_type')
            dd=residual.get(min(5,count),(0,0,0)) if (count>=3 and attack>0) else (0,0,0)
            if dd!=(0,0,0):
                out['first'][l]+=dd[0];out['second'][l]+=dd[1];out['third'][l]+=dd[2]
            meta[l]={'course':c,'count':count,'reasons':reasons,'delta':dd,'attack_rate':attack}
        return {p:norm(v) for p,v in out.items()},meta

    def water_tide(self, probs, race, actual, direct=None, tide_events=None):
        out={p:dict(v) for p,v in probs.items()}
        tide=precise_tide_factor(str(race.get('deadline') or ''),list(tide_events or []),self.m.course_rates,self.m.tide_rates)
        if tide:
            # Factors are course-based. Apply them to the boat occupying each actual course.
            for l,c in actual.items():
                for p in ('first','second','third'):out[p][l]*=tide['factors'][c][p]
            out={p:norm(v) for p,v in out.items()}
        d=(direct or {}).get('data') or {}
        wind=f(d.get('wind_speed'),0); wave=f(d.get('wave_height'),0)
        # General rough-water handling is deliberately weak: uncertainty shrink only.
        # It avoids inventing a directional course advantage when wind direction is numeric/unknown.
        rough=max(0.0,min(1.0,max((wind-3.0)/3.0,(wave-3.0)/3.0))) if (wind>=4 or wave>=4) else 0.0
        if rough>0:
            alpha=min(.04,.04*rough)
            for p in out:
                out[p]={l:(1-alpha)*out[p][l]+alpha*(100/6) for l in LANES}
                out[p]=norm(out[p])
        direction_text=str(d.get('wind_direction_label') or d.get('wind_direction_text') or d.get('wind_direction_name') or '')
        nw_final=False
        day=i(race.get('eventDay') or (race.get('race_meta') or {}).get('day_no') or 1,1)
        if day>=5 and wind>=4 and wave>=4 and ('北西' in direction_text or direction_text.upper() in {'NW','WNW','NNW'}):
            nw_final=True
            lane_for_course={c:l for l,c in actual.items()}
            # Fixed final-day NW-wave residual: keep 1-head, widen 2nd/3rd to outer pickup.
            for c,ds,dt in ((4,.8,.5),(5,.6,.6),(6,.9,1.2)):
                l=lane_for_course.get(c)
                if l:
                    out['second'][l]+=ds; out['third'][l]+=dt
            out={p:norm(v) for p,v in out.items()}
        return out,{'tide':tide,'wind_speed':wind,'wave_height':wave,'roughness':rough,'direction_text':direction_text,'nw_final_rule':nw_final}

    @staticmethod
    def build_tickets(maps, race, debug):
        first={l:f(maps['win'][str(l)]) for l in LANES}; second={l:f(maps['second'][str(l)]) for l in LANES}; third={l:f(maps['third'][str(l)]) for l in LANES}
        all_t=[]
        for a in LANES:
            for b in LANES:
                if b==a:continue
                for c in LANES:
                    if c in (a,b):continue
                    sc=first[a]/100*second[b]/100*third[c]/100
                    all_t.append(((a,b,c),sc))
        all_t.sort(key=lambda x:x[1],reverse=True)
        main=all_t[:6]; used={t for t,_ in main}
        head=main[0][0][0]
        deviation=[]
        for t,sc in all_t[6:]:
            if t in used:continue
            if t[0]==head:
                deviation.append((t,sc));used.add(t)
            if len(deviation)==2:break
        # Scenario-conditioned upset head: escape interaction first, then strongest compound attacker.
        attack_head=0
        ea=debug.get('escape_attack_sum') or {}
        if f(ea.get('first_transfer'))>=.6:attack_head=i(ea.get('attack_lane'))
        if not attack_head:
            comps=debug.get('compound_attack') or {}
            cand=[]
            for lk,m in comps.items():
                if isinstance(m,Mapping) and i(m.get('count'))>=4 and f(m.get('attack_rate'))>0:
                    cand.append((i(m.get('count')),first.get(i(lk),0),i(lk)))
            if cand:attack_head=max(cand)[2]
        if not attack_head:
            attack_head=sorted(LANES,key=lambda l:first[l],reverse=True)[1]
        actual=debug.get('actual_course') or {}; hc=i(actual.get(attack_head) if isinstance(actual,Mapping) else 0)
        upset_candidates=[]
        for t,sc in all_t:
            if t in used or t[0]!=attack_head:continue
            mult=1.0
            if hc:
                bc=i(actual.get(t[1]) if isinstance(actual,Mapping) else 0); cc=i(actual.get(t[2]) if isinstance(actual,Mapping) else 0)
                if bc==hc+1:mult*=1.75
                if cc in (hc+1,hc+2):mult*=1.35
            upset_candidates.append((t,sc*mult))
        upset_candidates.sort(key=lambda x:x[1],reverse=True)
        upset=[]
        for t,sc in upset_candidates:
            if t not in used:
                upset.append((t,sc));used.add(t)
            if len(upset)==2:break
        if len(upset)<2:
            for t,sc in all_t:
                if t not in used and t[0]!=head:
                    upset.append((t,sc));used.add(t)
                if len(upset)==2:break
        fmt=lambda arr:[{'combo':'-'.join(map(str,t)),'score':round(sc*100,4)} for t,sc in arr]
        return {'main':fmt(main),'deviation':fmt(deviation),'upset':fmt(upset),'scenarioHead':attack_head}

    @staticmethod
    def sab_score(maps, tickets, debug):
        win=sorted((f(v) for v in maps['win'].values()),reverse=True); head=win[0]; margin=head-win[1]
        if head>=50 and margin>=15:axis=25
        elif head>=45 and margin>=10:axis=22
        elif head>=38 and margin>=7:axis=18
        else:axis=12
        rel={l:(f(maps['second'][str(l)])+f(maps['third'][str(l)]))/2 for l in LANES}
        rs=sorted(rel.values(),reverse=True)
        opponent=20 if sum(rs[:3])>=55 else (17 if sum(rs[:3])>=48 else 14)
        live=(debug.get('live') or {}); pat=str(live.get('pattern') or '')
        ea=debug.get('escape_attack_sum') or {}; comps=debug.get('compound_attack') or {}
        strong_comp=any(isinstance(m,Mapping) and i(m.get('count'))>=4 and f(m.get('attack_rate'))>0 for m in comps.values())
        if f(ea.get('first_transfer'))>=1.0 or strong_comp:scenario=20
        elif pat and pat!='横一線':scenario=17
        else:scenario=13
        water=debug.get('water') or {}; tide_ok=bool(water.get('tide'))
        live_ok=bool(live); motor_ok=len(debug.get('motor') or {})==6; series_ok=len(debug.get('series') or {})==6
        data_score=5*sum((tide_ok,live_ok,motor_ok,series_ok))
        tcount=sum(len(tickets.get(k,[])) for k in ('main','deviation','upset'))
        unique=len({x.get('combo') for k in ('main','deviation','upset') for x in tickets.get(k,[])})
        ticket_score=15 if tcount==10 and unique==10 else (10 if tcount>=8 else 5)
        penalty=0; penalties=[]
        actual=debug.get('actual_course') or {}
        changed_courses=[l for l in LANES if isinstance(actual,Mapping) and i(actual.get(l),l)!=l]
        entry_shift_size=sum(abs(i(actual.get(l),l)-l) for l in LANES) if isinstance(actual,Mapping) else 0
        if changed_courses:penalty+=6;penalties.append('entry_change:-6')
        rough=f(water.get('roughness'))
        if rough>=.7:penalty+=4;penalties.append('rough_water:-4')
        multi_head_count=sum(1 for v in maps['win'].values() if f(v)>=15)
        if multi_head_count>=3:penalty+=10;penalties.append('multi_head:-10')
        total=max(0,min(100,axis+opponent+scenario+data_score+ticket_score-penalty))
        grade='S' if total>=80 else ('A' if total>=65 else ('B' if total>=50 else '見'))
        scenario_upset=f(ea.get('first_transfer'))>=1.0 or strong_comp
        sab_guard=(len(changed_courses)>=3 or entry_shift_size>=6) and multi_head_count>=3 and scenario_upset
        grade_cap='A' if sab_guard else None
        if sab_guard and grade=='S':grade='A'
        debug.update({
            'sab_guard':sab_guard,
            'grade_cap':grade_cap,
            'changed_courses':changed_courses,
            'entry_shift_size':entry_shift_size,
            'multi_head_count':multi_head_count,
            'scenario_upset':scenario_upset,
        })
        return grade,{'score':total,'axis':axis,'opponent':opponent,'scenario':scenario,'data':data_score,'ticket':ticket_score,'penalty':penalty,'penalties':penalties}

    def predict_final(self, race, exhibition, original, direct=None, tide_events=None):
        actual=self.actual_courses(race,exhibition)
        p,m0=self.base_actual_course(race,actual)
        p,mm=self.motor_base(p,race)
        p,sm=self.series(p,race)
        p,lm=self.live_layers(p,race,actual,exhibition,original)
        p,km=self.escape_attack_sum_interaction(p,race,actual,exhibition,original)
        p,cm=self.compound_attack(p,race,actual,exhibition,original)
        p,wm=self.water_tide(p,race,actual,direct,tide_events)
        maps={k:{str(l):round(p[{'win':'first','second':'second','third':'third'}[k]][l],2) for l in LANES} for k in ('win','second','third')}
        debug={'actual_course':actual,'course_remap':m0,'motor':mm,'series':sm,'live':lm,'escape_attack_sum':km,'compound_attack':cm,'water':wm,'result_used':False,'odds_used':False}
        tickets=self.build_tickets(maps,race,debug)
        sab,sab_meta=self.sab_score(maps,tickets,debug);debug['sab']=sab_meta
        main=[x['combo'] for x in tickets['main']]; dev=[x['combo'] for x in tickets['deviation']]; upset=[x['combo'] for x in tickets['upset']]
        return {'engine':'shimonoseki_engine_v6.0','engineVersion':'6.0','phase':'final','status':'complete','probabilities':maps,**maps,'sab':sab,'tickets':tickets,'ai':main,'balance':dev,'aiUpset':upset,'debug':debug}
