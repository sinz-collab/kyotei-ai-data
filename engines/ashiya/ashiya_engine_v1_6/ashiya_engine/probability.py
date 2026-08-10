
from __future__ import annotations
from .utils import normalize, as_float, as_int, norm_reg_no

REL={'A':1.0,'B':0.65,'C':0.35}
def pct(x): return as_float(x)/100.0

def db_prior(racer, db):
    lane=as_int(racer.get('lane')); course=as_int(racer.get('actual_course') or racer.get('entry_course'),lane)
    rows=db.lookup(racer.get('reg_no') or racer.get('player_id'),lane,course,racer.get('name') or racer.get('player_name'))
    weights=[]; vals=[]
    for key,w0 in [('course',0.50),('lane',0.25),('shift',0.25)]:
        r=rows.get(key)
        if not r: continue
        rel=REL.get(str(r.get('reliability')),0.25); starts=min(1.0,as_float(r.get('starts'))/20)
        w=w0*rel*starts
        weights.append(w); vals.append((pct(r.get('win_rate')),pct(r.get('second_rate')),pct(r.get('third_rate'))))
    if not weights: return None,rows
    s=sum(weights); return tuple(sum(v[i]*w for v,w in zip(vals,weights))/s for i in range(3)),rows

def blend(model_probs, racers, db, coverage):
    priors=[]; lookups=[]
    lane_win=[0.54,0.15,0.12,0.10,0.06,0.03]
    lane_second=[0.18,0.25,0.20,0.17,0.12,0.08]
    lane_third=[0.11,0.18,0.21,0.20,0.17,0.13]
    for r in racers:
        prior,lk=db_prior(r,db); lookups.append(lk)
        lane=as_int(r.get('lane'),1)-1
        priors.append(prior or (lane_win[lane],lane_second[lane],lane_third[lane]))
    model_weight=max(0.35,min(0.78,0.35+coverage*0.50))
    out={}
    for idx,k in enumerate(['win','second','third']):
        raw=[model_weight*m+(1-model_weight)*priors[i][idx] for i,m in enumerate(model_probs[k])]
        # Small and bounded player-course adjustment only.
        for i,lk in enumerate(lookups):
            weak=lk.get('weakness')
            if weak and str(weak.get('reliability')) in ('A','B'): raw[i]*=0.94
        out[k]=normalize(raw)
    return out,{'model_weight':round(model_weight,4),'player_lookups':lookups}


def _finish_value(text):
    s=str(text or '')
    for i in range(1,7):
        if s.startswith(str(i)):
            return i
    return None

def _season_components(racer, actual_course):
    runs=racer.get('season_runs') or []
    vals=[]; same=[]
    for idx,x in enumerate(runs):
        f=_finish_value(x.get('finish'))
        if not f: continue
        # Later records are not assumed newer; use bounded uniform form score.
        score=(6-f)/5
        vals.append(score)
        c=as_int(x.get('entry_course') or x.get('course'),0)
        if c==actual_course: same.append(score)
    if not vals: return 0.5,0.5,0
    overall=sum(vals)/len(vals)
    same_score=sum(same)/len(same) if same else overall
    reliability=min(1.0,len(vals)/6)
    return overall,same_score,reliability

def _quality_rank(racer, *keys):
    for key in keys:
        rank=as_int(racer.get(key),0)
        if 1<=rank<=6: return (7-rank)/6
    return 0.5

def day_profile(race):
    label=str(race.get('seriesDay') or race.get('eventDayLabel') or '')
    day=as_int(race.get('eventDay') or (race.get('race_meta') or {}).get('day_no'),0)
    final=('最終日' in label) or day>=4
    if final:
        return {'name':'final_day','base':0.40,'motor':0.08,'season':0.12,'season_course':0.06,'exhibition':0.14,'environment':0.10,'scenario':0.10}
    if day<=1 and day>0:
        return {'name':'opening_day','base':0.40,'motor':0.14,'season':0.06,'season_course':0.00,'exhibition':0.20,'environment':0.10,'scenario':0.10}
    return {'name':'middle_day','base':0.40,'motor':0.10,'season':0.10,'season_course':0.00,'exhibition':0.20,'environment':0.10,'scenario':0.10}

def apply_day_adjustment(probs, racers, race):
    profile=day_profile(race)
    out={k:list(v) for k,v in probs.items()}
    motor=[]; season=[]; same_course=[]; exhibit=[]
    for r in racers:
        course=as_int(r.get('actual_course') or r.get('entry_course'),as_int(r.get('lane'),1))
        m2=as_float(r.get('motor_2') or r.get('motor_2_rate'),33.0)/100
        m3=as_float(r.get('motor_3') or r.get('motor_3_rate'),50.0)/100
        motor.append(max(0,min(1,0.65*m2+0.35*m3)))
        ss,sc,rel=_season_components(r,course)
        season.append(0.5+(ss-0.5)*rel)
        same_course.append(0.5+(sc-0.5)*rel)
        ex=0.34*_quality_rank(r,'exhibition_rank')+0.33*_quality_rank(r,'lap_rank','original_lap_rank')+0.33*_quality_rank(r,'sum_rank','original_sum_rank')
        exhibit.append(ex)
    # Convert component quality around neutral 0.5 into bounded multipliers.
    for i in range(len(racers)):
        q=(profile['motor']*(motor[i]-0.5)+profile['season']*(season[i]-0.5)+profile['season_course']*(same_course[i]-0.5)+profile['exhibition']*(exhibit[i]-0.5))
        # Final-day exhibition is capped; season has more effect.
        win_mult=max(0.82,min(1.18,1+1.8*q))
        sec_mult=max(0.84,min(1.16,1+1.5*q))
        third_mult=max(0.86,min(1.14,1+1.2*q))
        out['win'][i]*=win_mult; out['second'][i]*=sec_mult; out['third'][i]*=third_mult
    for k in out: out[k]=normalize(out[k])
    audit={'profile':profile,'motor_scores':[round(x,4) for x in motor],'season_scores':[round(x,4) for x in season],'same_course_season_scores':[round(x,4) for x in same_course],'exhibition_scores':[round(x,4) for x in exhibit]}
    return out,audit

GRADE_SCORE={'A1':1.0,'A2':0.78,'B1':0.48,'B2':0.22}

def _player_class_score(racer):
    grade=str(racer.get('class') or racer.get('grade') or '').upper()
    g=GRADE_SCORE.get(grade,0.45)
    nat=as_float(racer.get('nat_win') or racer.get('national_win_rate'),0.0)
    nat_level=max(0.0,min(1.0,(nat-2.0)/6.0))
    return 0.60*g+0.40*nat_level

def _local_performance_score(racer):
    nat=as_float(racer.get('nat_win') or racer.get('national_win_rate'),0.0)
    lw=as_float(racer.get('local_win') or racer.get('local_win_rate'),0.0)
    l2=as_float(racer.get('local_2') or racer.get('local_2ren_rate'),0.0)
    l3=as_float(racer.get('local_3') or racer.get('local_3ren_rate'),0.0)
    win=max(0.0,min(1.0,(lw-2.0)/7.0))
    two=max(0.0,min(1.0,l2/75.0))
    three=max(0.0,min(1.0,l3/90.0))
    edge=max(-1.0,min(1.0,(lw-nat)/2.0)) if nat>0 and lw>0 else 0.0
    return max(0.0,min(1.0,0.34*win+0.26*two+0.18*three+0.22*((edge+1)/2)))

def apply_practical_support(probs, racers, race):
    """Bundle class, local record, season form, motor and same-course season form.

    This layer is deliberately bounded. It cannot create an attacker; it only
    strengthens or weakens the model before slit/scenario logic is applied.
    Final day emphasizes season evidence over raw motor numbers.
    """
    profile=day_profile(race)
    final_day=profile['name']=='final_day'
    weights={'season':0.30,'local':0.25,'class':0.20,'motor':0.15,'season_course':0.10} if final_day else {'season':0.24,'local':0.22,'class':0.20,'motor':0.22,'season_course':0.12}
    scores=[]
    out={k:list(v) for k,v in probs.items()}
    for r in racers:
        course=as_int(r.get('actual_course') or r.get('entry_course'),as_int(r.get('lane'),1))
        ss,sc,rel=_season_components(r,course)
        season=0.5+(ss-0.5)*rel
        same=0.5+(sc-0.5)*rel
        m2=as_float(r.get('motor_2') or r.get('motor_2_rate'),33.0)/100
        m3=as_float(r.get('motor_3') or r.get('motor_3_rate'),50.0)/100
        motor=max(0.0,min(1.0,0.65*m2+0.35*m3))
        local=_local_performance_score(r)
        cls=_player_class_score(r)
        total=(weights['season']*season+weights['local']*local+weights['class']*cls+weights['motor']*motor+weights['season_course']*same)
        scores.append({'total':total,'season':season,'local':local,'class':cls,'motor':motor,'season_course':same})
    # Center around field mean so only relative strength changes probabilities.
    mean=sum(x['total'] for x in scores)/len(scores)
    for i,s in enumerate(scores):
        delta=max(-0.18,min(0.18,s['total']-mean))
        out['win'][i]*=max(0.84,min(1.18,1+0.95*delta))
        out['second'][i]*=max(0.88,min(1.14,1+0.72*delta))
        out['third'][i]*=max(0.90,min(1.12,1+0.55*delta))
    for k in out: out[k]=normalize(out[k])
    audit={'profile':profile['name'],'weights':weights,'field_mean':round(mean,4),'scores':[]}
    for r,s in zip(racers,scores):
        audit['scores'].append({'lane':as_int(r.get('lane')),'name':r.get('name'),'total':round(s['total'],4),'season':round(s['season'],4),'local':round(s['local'],4),'class':round(s['class'],4),'motor':round(s['motor'],4),'season_course':round(s['season_course'],4)})
    return out,audit
