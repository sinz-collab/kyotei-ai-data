from __future__ import annotations
import argparse, itertools, json, math
from pathlib import Path
from typing import Any
import joblib
import numpy as np
import pandas as pd

FEATURES_FILE='tokoname_model_features_v1_0.pkl'
MODEL_FILES={'win':'tokoname_model_win_v1_0.pkl','top2':'tokoname_model_top2_v1_0.pkl','top3':'tokoname_model_top3_v1_0.pkl'}

def f(v, default=0.0):
    try:
        if v in (None,'','-'): return default
        return float(str(v).replace('%','').strip())
    except Exception: return default

def rank_asc(vals):
    order=sorted(range(len(vals)), key=lambda i:(vals[i],i))
    out=[0]*len(vals)
    for k,i in enumerate(order,1): out[i]=k
    return out

def normalize(vals,total=100.0):
    a=np.maximum(np.asarray(vals,dtype=float),1e-9)
    return (a/a.sum()*total).tolist()

def isotonic_triplet(w,t2,t3):
    # Small deterministic monotonic projection.
    a=sorted([max(0.0,w),max(0.0,t2),max(0.0,t3)])
    return a[0],a[1],a[2]

def build_features(payload):
    race=payload['race']; racers=race['racers']; ex=payload['exhibition']['data']['entries']
    ex_by={int(x['lane']):x for x in ex}
    sts=[f(ex_by[int(r['lane'])]['start_time'],.20) for r in racers]
    ets=[f(ex_by[int(r['lane'])]['exhibition_time'],6.90) for r in racers]
    st_ranks=rank_asc(sts); et_ranks=rank_asc(ets)
    stmean=float(np.mean(sts)); etmean=float(np.mean(ets))
    dt=pd.Timestamp(payload['date'])
    season={12:0,1:0,2:0,3:1,4:1,5:1,6:2,7:2,8:2,9:3,10:3,11:3}[dt.month]
    rows=[]
    for i,r in enumerate(racers):
        lane=int(r['lane']); course=int(r.get('actual_course') or r.get('entry_course') or lane)
        rows.append({
          'race':int(race['race']), 'lane':lane, 'course':course,
          'course_diff_from_lane':course-lane,'is_same_lane_course':int(course==lane),
          'is_inner_change':int(course<lane),'is_outer_change':int(course>lane),
          'player_id':int(r.get('player_id') or payload.get('player_ids',{}).get(str(lane),0)),
          'motor_no':int(f(r.get('motor_no'),0)),'boat_no':int(f(r.get('boat_no'),0)),
          'st':sts[i],'st_rank_in_race':st_ranks[i],'st_mean_in_race':stmean,
          'st_diff_from_race_mean':sts[i]-stmean,'race_st_mean':stmean,'race_st_min':min(sts),'race_st_max':max(sts),
          'exhibition':ets[i],'exhibition_rank_in_race':et_ranks[i],'exhibition_mean_in_race':etmean,
          'exhibition_diff_from_race_mean':ets[i]-etmean,'race_exhibition_mean':etmean,
          'race_exhibition_min':min(ets),'race_exhibition_max':max(ets),
          'year':dt.year,'month':dt.month,'day':dt.day,'season_code':season})
    return pd.DataFrame(rows),st_ranks,et_ranks

def apply_corrections(payload,p1,p2,p3,st_ranks,et_ranks):
    racers=payload['race']['racers']
    event_day=int(payload.get('eventDay') or payload.get('event_day') or payload.get('race',{}).get('day_no') or 1)
    orig={int(x['lane']):x for x in payload.get('original_exhibition',{}).get('data',{}).get('entries',[])}
    n=len(racers); dw=np.zeros(n); d2=np.zeros(n); d3=np.zeros(n); reasons=[[] for _ in racers]

    # Symmetric class/local-course layer. The base model already includes lane/course,
    # so this layer tests whether that baseline deserves to be maintained or reduced.
    class_adj={
        'A1':(1.8,1.2,0.7),
        'A2':(0.7,0.6,0.4),
        'B1':(0.0,0.0,0.0),
        'B2':(-1.4,-0.9,-0.5),
    }
    for i,r in enumerate(racers):
        lane=int(r['lane'])
        grade=str(r.get('class','B1')).upper()
        a1,a2,a3=class_adj.get(grade,(0.0,0.0,0.0))
        dw[i]+=a1; d2[i]+=a2; d3[i]+=a3
        if grade=='A1': reasons[i].append('A1級別加点')
        elif grade=='A2': reasons[i].append('A2級別小幅加点')
        elif grade=='B2': reasons[i].append('B2級別減点')
        # Positive situational signals convert to win probability at different
        # rates by class. Keep class base itself separate from this multiplier.
        head_signal_start=dw[i]

        local_win=f(r.get('local_win'),0); nat_win=f(r.get('nat_win'),0)
        local2=f(r.get('local_2'),0); nat2=f(r.get('nat_2'),0)
        local3=f(r.get('local_3'),0); nat3=f(r.get('nat_3'),0)
        motor3=f(r.get('motor_3'),0); boat3=f(r.get('boat_3'),0)

        # Local performance: add and subtract symmetrically. Use conservative caps
        # because sample counts are not yet carried in the live payload.
        if local_win>=6.5:
            dw[i]+=1.4; d2[i]+=0.6; reasons[i].append('当地勝率上位')
        elif 0<local_win<4.0:
            dw[i]-=1.2; d2[i]-=0.5; reasons[i].append('当地勝率下位')
        if nat_win and local_win:
            gap=local_win-nat_win
            if gap>=1.0:
                dw[i]+=0.8; d2[i]+=0.5; reasons[i].append('当地成績が全国超え')
            elif gap<=-1.0:
                dw[i]-=0.8; d2[i]-=0.5; reasons[i].append('当地成績が全国割れ')
        if local3>=60:
            d2[i]+=0.8; d3[i]+=1.2; reasons[i].append('当地3連率上位')
        elif 0<local3<=25:
            dw[i]-=1.0; d2[i]-=0.8; d3[i]-=0.5; reasons[i].append('当地3連率下位')

        # Day 1 has no season evidence yet, so motor quality carries more weight.
        # This is a stage-weight change, not a result-specific correction.
        if event_day == 1:
            if motor3 >= 55:
                dw[i]+=1.4; d2[i]+=1.2; d3[i]+=1.3; reasons[i].append('初日モーター3連率上位・強化')
            elif motor3 >= 50:
                dw[i]+=0.6; d2[i]+=0.6; d3[i]+=0.7; reasons[i].append('初日モーター3連率良好')
            elif 0 < motor3 < 42:
                dw[i]-=0.8; d2[i]-=0.6; d3[i]-=0.3; reasons[i].append('初日モーター3連率下位')
        else:
            if motor3>=55:
                dw[i]+=0.4; d2[i]+=0.6; d3[i]+=0.8; reasons[i].append('モーター3連率上位')
            elif 0<motor3<42:
                dw[i]-=0.5; d2[i]-=0.4; reasons[i].append('モーター3連率下位')
        if boat3>=65:
            d2[i]+=0.4; d3[i]+=0.8; reasons[i].append('ボート3連率上位')
        elif 0<boat3<40:
            d3[i]-=0.5; reasons[i].append('ボート3連率下位')

        if et_ranks[i]==1:
            dw[i]+=1.0; d2[i]+=0.8; d3[i]+=0.5; reasons[i].append('展示1位')
        elif et_ranks[i]>=5:
            dw[i]-=0.7; d2[i]-=0.3; reasons[i].append('展示下位')
        # Start reproducibility layer: exhibition ST is not judged alone.
        # Local ST confirms whether the exhibition slit is reproducible or whether
        # a deliberately conservative exhibition can be corrected in the race.
        ex_st=f(payload['exhibition']['data']['entries'][i].get('start_time'),.20)
        local_st=f(r.get('local_st'),0)
        ex_good=ex_st<=0.15
        ex_bad=ex_st>=0.20
        # Local ST tier is a stronger reproducibility signal than one exhibition start.
        if 0 < local_st <= 0.12:
            local_w, local_2, local_3 = 2.8, 1.5, 0.5
            reasons[i].append('当地ST非常に優秀')
        elif local_st <= 0.14 and local_st > 0:
            local_w, local_2, local_3 = 1.8, 1.0, 0.5
            reasons[i].append('当地ST優秀')
        elif local_st <= 0.16 and local_st > 0:
            local_w, local_2, local_3 = 0.8, 0.5, 0.2
            reasons[i].append('当地ST良好')
        elif local_st >= 0.21:
            local_w, local_2, local_3 = -2.5, -1.0, -0.5
            reasons[i].append('当地ST明確に弱い')
        elif local_st >= 0.19:
            local_w, local_2, local_3 = -1.2, -0.5, 0.0
            reasons[i].append('当地ST弱い')
        else:
            local_w, local_2, local_3 = 0.0, 0.0, 0.0
        dw[i]+=local_w; d2[i]+=local_2; d3[i]+=local_3
        local_good=0<local_st<=0.16
        local_bad=local_st>=0.19
        if ex_good and local_good:
            dw[i]+=0.7; d2[i]+=0.4; d3[i]+=0.2; reasons[i].append('展示ST良好×当地ST良好')
        elif ex_bad and local_good:
            dw[i]+=0.8; d2[i]+=0.5; d3[i]+=0.2; reasons[i].append('本番修正型ST加点')
        elif ex_good and local_bad:
            dw[i]+=0.1; reasons[i].append('展示先行型ST加点抑制')
        elif ex_bad and local_bad:
            dw[i]-=1.0; d2[i]-=0.6; d3[i]-=0.3; reasons[i].append('展示ST悪化×当地ST悪化')
        elif st_ranks[i]==1:
            dw[i]+=0.3; d2[i]+=0.2; reasons[i].append('展示ST先行')

        if lane in orig:
            o=orig[lane]
            laps=[f(x['lap_time'],99) for x in orig.values()]
            turns=[f(x['turn_time'],99) for x in orig.values()]
            straights=[f(x['straight_time'],99) for x in orig.values()]
            if f(o['lap_time'],99)==min(laps):
                dw[i]+=0.9; d2[i]+=1.0; d3[i]+=0.4; reasons[i].append('1周最上位')
            if f(o['turn_time'],99)==min(turns):
                d2[i]+=1.0; d3[i]+=1.0; reasons[i].append('回り足最上位')
            if f(o['straight_time'],99)==min(straights):
                dw[i]+=0.8; d2[i]+=0.4; reasons[i].append('直線最上位')

        # Grade-dependent conversion of positive signals into first-place rate.
        # B-class boats can still create the race or remain in 2nd/3rd, but a
        # good ST/motor/exhibition should not translate into A1-level win gain.
        win_conversion={'A1':1.00,'A2':0.85,'B1':0.55,'B2':0.35}.get(grade,0.55)
        situational_delta=dw[i]-head_signal_start
        if situational_delta>0:
            dw[i]=head_signal_start+situational_delta*win_conversion
            if win_conversion<1.0:
                reasons[i].append(f'級別別頭加点変換率{int(win_conversion*100)}%')

    # Explicit erosion of the lane-1 baseline when multiple negative signals align.
    r1=racers[0]
    negative=0
    if str(r1.get('class','B1')).upper() in ('B1','B2'): negative+=1
    if 0<f(r1.get('local_win'),0)<5.0: negative+=1
    if f(r1.get('boaters_escape_rate'),0)<45: negative+=1
    if et_ranks[0]>=5: negative+=1
    # A superior attacking boat in 2/3/4 with class + direct/exhibition support.
    attack_candidates=[]
    for j in (1,2,3):
        grade=str(racers[j].get('class','B1')).upper()
        strength=(2 if grade=='A1' else 1 if grade=='A2' else 0)
        strength += 1 if st_ranks[j]<=2 else 0
        if j+1 in orig:
            straights=[f(x['straight_time'],99) for x in orig.values()]
            strength += 1 if f(orig[j+1]['straight_time'],99)==min(straights) else 0
        attack_candidates.append((strength,j))
    best_strength,best_j=max(attack_candidates)
    if best_strength>=3: negative+=1

    if negative>=2:
        penalty=min(5.5,1.2*negative)
        dw[0]-=penalty
        # Failure to win does not mean disappearance: move part into place/show.
        d2[0]+=penalty*0.35
        d3[0]+=penalty*0.45
        reasons[0].append(f'1コース優位減点({negative}条件)')
        if best_strength>=3:
            dw[best_j]+=min(3.0,penalty*0.45)
            d2[best_j]+=min(1.8,penalty*0.25)
            reasons[best_j].append('内枠弱化時の攻め艇加点')
    else:
        escape=f(r1.get('boaters_escape_rate'),0)
        if escape>=55:
            dw[0]+=1.0; reasons[0].append('高逃げ率で基礎優位維持')

    # Slit interaction.
    sts=[f(x['start_time'],.20) for x in payload['exhibition']['data']['entries']]
    if sts[1]-min(sts[2:4])>=0.08:
        dw[2]+=0.7; dw[3]+=0.5; d2[2]+=0.5; d2[3]+=0.5
        reasons[2].append('2艇遅れによる3攻め余地'); reasons[3].append('2艇遅れによる4連動余地')

    # Keep ordinary adjustments bounded, while allowing multi-signal lane-1 erosion.
    dw=np.clip(dw,-7,7); d2=np.clip(d2,-5,5); d3=np.clip(d3,-5,5)
    return normalize(np.asarray(p1)+dw),normalize(np.asarray(p2)+d2),normalize(np.asarray(p3)+d3),reasons

def ticket_scores(p1,p2,p3):
    scores=[]
    for a,b,c in itertools.permutations(range(6),3):
        # Position-marginal product with exclusion renormalization approximation.
        s=(p1[a]/100)*(p2[b]/100)*(p3[c]/100)
        scores.append((s,(a+1,b+1,c+1)))
    z=sum(s for s,_ in scores)
    return sorted([(s/z*100,t) for s,t in scores],reverse=True)

def scenario_name(head): return {1:'1逃げ中心',2:'2差し中心',3:'3攻め中心',4:'4攻め・差し中心',5:'5まくり差し中心',6:'6外残り中心'}[head]

def select_tickets(scores,p1,p2,p3):
    """Select 10 tickets with scenario-role consistency.

    Main tickets are not a flat top-N of marginal products. First fix the most
    likely head, then the most likely second-place boat for that head scenario,
    and rank third-place candidates by their final third-place probability.
    """
    score_map={t:s for s,t in scores}
    used=set(); main=[]; dev=[]; upset=[]
    primary=int(np.argmax(p1))+1

    # Second-place roles under the primary-head scenario.
    second_order=[i+1 for i in sorted(range(6), key=lambda i:(-p2[i], i)) if i+1!=primary]

    def add(bucket,t):
        if t in used: return False
        bucket.append((score_map.get(t,0.0),t)); used.add(t); return True

    # Main scenario representative: primary head + best second + best available third.
    # Third candidates are ordered by final third-place rate, matching the stated
    # operational rule used in manual Tokoname predictions.
    for second in second_order:
        third_order=[i+1 for i in sorted(range(6), key=lambda i:(-p3[i], i)) if i+1 not in (primary,second)]
        for third in third_order:
            add(main,(primary,second,third))
            if len(main)>=6: break
        if len(main)>=6: break

    # Deviation: head shifts, while the primary head remains in the top two.
    for s,t in scores:
        if len(dev)>=2: break
        if t not in used and t[0]!=primary and primary in t[:2]:
            dev.append((s,t)); used.add(t)

    # Upset: non-primary head and no duplication.
    for s,t in scores:
        if len(upset)>=2: break
        if t not in used and t[0]!=primary:
            upset.append((s,t)); used.add(t)

    # Strict fill guarantee.
    for bucket,target in ((main,6),(dev,2),(upset,2)):
        for s,t in scores:
            if len(bucket)>=target: break
            if t not in used:
                bucket.append((s,t)); used.add(t)
    return main,dev,upset

def sab_grade(p1,scores,data_complete=True):
    q=sorted(p1,reverse=True); head_gap=q[0]-q[1]; top6=sum(s for s,_ in scores[:6])
    score=50+min(20,head_gap*1.2)+min(15,top6*1.5)+(10 if data_complete else 0)
    score=max(0,min(100,round(score)))
    return ('S' if score>=80 else 'A' if score>=65 else 'B'),score

def predict(payload,model_dir):
    X,st_ranks,et_ranks=build_features(payload)
    features=joblib.load(Path(model_dir)/FEATURES_FILE)
    models={k:joblib.load(Path(model_dir)/v) for k,v in MODEL_FILES.items()}
    raw={k:m.predict_proba(X[features])[:,1] for k,m in models.items()}
    exact=[isotonic_triplet(*v) for v in zip(raw['win'],raw['top2'],raw['top3'])]
    p1=normalize([x[0] for x in exact]); p2=normalize([x[1]-x[0] for x in exact]); p3=normalize([x[2]-x[1] for x in exact])
    p1,p2,p3,reasons=apply_corrections(payload,p1,p2,p3,st_ranks,et_ranks)
    scores=ticket_scores(p1,p2,p3); main,dev,upset=select_tickets(scores,p1,p2,p3)
    complete_count=sum([bool(payload.get('direct')),bool(payload.get('exhibition')),bool(payload.get('original_exhibition')),bool(payload['race'].get('setsukan')),bool(payload.get('tide'))])
    grade,sab_score=sab_grade(p1,scores,complete_count>=4)
    if complete_count<4:
        sab_score=max(0,sab_score-(4-complete_count)*6)
        grade='S' if sab_score>=80 else 'A' if sab_score>=65 else 'B'
    racers=payload['race']['racers']
    probs=[{'lane':i+1,'name':racers[i]['name'],'win':round(p1[i],1),'second':round(p2[i],1),'third':round(p3[i],1),'top3':round(p1[i]+p2[i]+p3[i],1),'reasons':reasons[i]} for i in range(6)]
    primary=int(np.argmax(p1))+1
    def pack(xs,cat): return [{'combination':'-'.join(map(str,t)),'score_pct':round(s,3),'category':cat} for s,t in xs]
    return {'venue':'tokoname','date':payload['date'],'race_no':payload['race']['race'],'engine':'tokoname_engine_v1.6','stage':'final','probabilities':probs,'scenario':{'primary':scenario_name(primary),'head':primary,'head_gap':round(sorted(p1,reverse=True)[0]-sorted(p1,reverse=True)[1],1)},'sab':{'grade':grade,'score':sab_score,'independent_of_ticket_count':True},'tickets':{'main':pack(main,'main'),'deviation':pack(dev,'deviation'),'upset':pack(upset,'upset')},'data_flags':{'entry_change':bool(payload['direct']['data'].get('entry_changed')),'direct':True,'exhibition':True,'original_exhibition':bool(payload.get('original_exhibition')),'setsukan':bool(payload['race'].get('setsukan')),'tide':bool(payload.get('tide'))}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input'); ap.add_argument('--models',default=str(Path(__file__).resolve().parents[1]/'models')); ap.add_argument('-o','--output')
    a=ap.parse_args(); payload=json.loads(Path(a.input).read_text(encoding='utf-8')); out=predict(payload,a.models); text=json.dumps(out,ensure_ascii=False,indent=2)
    if a.output: Path(a.output).write_text(text,encoding='utf-8')
    else: print(text)
if __name__=='__main__': main()
