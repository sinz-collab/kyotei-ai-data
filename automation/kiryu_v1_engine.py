#!/usr/bin/env python3
"""Frozen Kiryu v1 inference (C + variable escape, D/.15, independent 6+4)."""
from __future__ import annotations

import itertools, io, json, math, zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

ROOT = Path(__file__).resolve().parent
ASSET = ROOT / "models" / "kiryu_v1"
CFG = json.loads((ROOT / "kiryu_v1_config.json").read_text(encoding="utf-8"))
FEATURES = ["pre_lane","race_no","event_day","month"] + [f"{p}_{n}" for p in ("ath","course","motor","boat") for n in ("win","place","second","third","count")] + [x+"_rel" for x in ("ath_win","ath_place","course_win","course_place","motor_win","motor_place")]
PERMS = np.array(list(itertools.permutations(range(6),3)), dtype=int); A,B,C = PERMS.T
PAIRS = np.array(list(itertools.permutations(range(6),2)), dtype=int); AA,BB = PAIRS.T

def _num(v, default=np.nan):
    try:
        x=float(str(v).replace("%", "")); return x if np.isfinite(x) else default
    except (TypeError,ValueError): return default

def _softmax(z):
    z=np.asarray(z,float); z=z-np.max(z); e=np.exp(z); return e/e.sum()

class KiryuV1:
    def __init__(self):
        z=zipfile.ZipFile(ASSET/"kiryu_ai_v1_7097r_handoff.zip")
        h=pd.read_csv(z.open("kiryu_7097r_boat_rows.csv"),low_memory=False)
        h["win"]=(h.outcome_finish_rank==1).astype(int); h["second"]=(h.outcome_finish_rank==2).astype(int); h["third"]=(h.outcome_finish_rank==3).astype(int); h["top3"]=h.outcome_finish_rank.isin([1,2,3]).astype(int)
        h["athlete_lane"]=h.pre_reg_no.astype(str)+"_"+h.pre_lane.astype(str)
        h["motor_cycle"]=h.year.astype(str)+"_"+h.pre_motor_no.astype(str); h["boat_cycle"]=h.year.astype(str)+"_"+h.pre_boat_no.astype(str)
        self.lookups={}
        for key,p in (("pre_reg_no","ath"),("athlete_lane","course"),("motor_cycle","motor"),("boat_cycle","boat")):
            self.lookups[p]=h.groupby(key,dropna=False).agg(n=("win","size"),w=("win","sum"),p=("top3","sum"),s=("second","sum"),t=("third","sum"))
        old=h[h.race_date<"2025-01-01"]; counts=np.zeros(120); ix={tuple(x):i for i,x in enumerate(PERMS)}
        ct2=np.zeros((6,6)); ct3=np.zeros((6,6,6))
        for _,g in old.groupby("race_id",sort=False):
            f=g.sort_values("pre_lane").outcome_finish_rank.to_numpy()
            if len(f)==6 and {1,2,3}.issubset(set(f)):
                abc=tuple(np.argsort(f)[:3]); counts[ix[abc]]+=1
                if np.isfinite(f).all(): ct2[abc[0],abc[1]]+=1; ct3[abc]+=1
        self.prior_rel=np.log(((counts+10)/(counts.sum()+1200))*120)
        q2=np.zeros((6,6)); q3=np.zeros((6,6,6))
        for a in range(6):
            ok=np.array([j!=a for j in range(6)]); q2[a,ok]=(ct2[a,ok]+6)/(ct2[a,ok].sum()+30)
            for b in range(6):
                if a==b: continue
                ok3=np.array([c!=a and c!=b for c in range(6)]); q3[a,b,ok3]=(ct3[a,b,ok3]+10)/(ct3[a,b,ok3].sum()+40)
        self.hist2_pair=np.log(np.maximum(q2*5,1e-8)); self.hist3=np.log(np.maximum(q3[A,B,C]*4,1e-8))
        self.win=CatBoostClassifier(); self.win.load_model(str(ASSET/"win.cbm"))
        self.second=CatBoostClassifier(); self.second.load_model(str(ASSET/"second_fast.cbm"))
        self.third=CatBoostClassifier(); self.third.load_model(str(ASSET/"third_fast.cbm"))

    def _matrix(self,race,date):
        racers=sorted(race["racers"],key=lambda r:int(r.get("lane",99))); assert len(racers)==6
        year=int(date[:4]); rows=[]
        for r in racers:
            lane=int(r["lane"]); rc=r.get("recent_course_6m") or {}; reg=str(r.get("player_id") or rc.get("registration_no") or "")
            row={"pre_lane":lane,"race_no":int(race["race"]),"event_day":int(race.get("eventDay") or 1),"month":int(date[5:7])}
            keys={"ath":reg,"course":reg+"_"+str(lane),"motor":f"{year}_{r.get('motor_no')}","boat":f"{year}_{r.get('boat_no')}"}
            for p,key in keys.items():
                try: s=self.lookups[p].loc[key]; n=float(s.n)
                except KeyError: s=None; n=0
                for fld,col,prior in (("win","w",.17),("place","p",.5),("second","s",.17),("third","t",.17)):
                    row[f"{p}_{fld}"]=((float(getattr(s,col)) if s is not None else 0)+prior*12)/(n+12)
                row[f"{p}_count"]=math.log1p(n)
            rows.append(row)
        for name in ("ath_win","ath_place","course_win","course_place","motor_win","motor_place"):
            mean=np.mean([r[name] for r in rows])
            for r in rows:r[name+"_rel"]=r[name]-mean
        return np.array([[r[k] for k in FEATURES] for r in rows],float),racers

    @staticmethod
    def _series(r):
        runs=[]
        for g in r.get("season_groups") or []:
            for x in g.get("runs") or []:
                f=str(x.get("finish") or ""); st=_num(x.get("st"))
                if f[:1].isdigit(): runs.append((int(f[:1]),st))
        rank=sum((3.5-f)/2.5 for f,_ in runs)/(len(runs)+3.0)
        st=sum((.16-x)/.12 for _,x in runs if np.isfinite(x))/(len(runs)+4.0)
        return rank,st

    def _first(self,X,racers,entry_changed):
        p=self.win.predict_proba(X)[:,1]; p/=p.sum()
        cfg=CFG["firstCorrection"]; raw=[]
        fields=("nat_win","local_win","nat_2","local_2","motor_2","motor_3")
        vals=[[ _num(r.get(k),0) for r in racers] for k in fields]
        series=list(zip(*(self._series(r) for r in racers)))
        vals += [list(series[0]),list(series[1])]
        for i,(arr,scale) in enumerate(zip(vals,cfg["scales"])):
            a=np.asarray(arr,float); limit=3 if i<6 else 2; raw.append(np.clip((a-a.mean())/scale,-limit,limit))
        p=_softmax(np.log(np.maximum(p,1e-12))+np.stack(raw,axis=1)@np.asarray(cfg["coefficients"]))
        r1=racers[0]; starts=_num(r1.get("boaters_kimarite_starts")); esc=_num(r1.get("boaters_escape_rate"))/100
        def z(v,reverse=False):
            v=np.asarray(v,float); q=(v-v.mean())/(v.std(ddof=1)+1e-6); return -q if reverse else q
        strength=.35*z(vals[0])+.25*z(vals[1])+.20*z(vals[4])
        st=np.array([_num(r.get("avg_st"),.16) for r in racers]); strength+=.20*z(st,True)
        threat=float(strength[1:].max()-strength[0])
        if np.isfinite(starts) and np.isfinite(esc) and threat>0 and p[0]<.55 and esc<.55 and not entry_changed:
            shrunk=(starts*esc+5*.48)/(starts+5); p[0]*=math.exp(-.25*max(0,.55-shrunk)/.10); p/=p.sum()
        return p

    def _conditionals(self,X):
        idx={k:i for i,k in enumerate(FEATURES)}
        def col(x,n): return x[idx[n]]
        def ctx(base,leader,ll,runner=None,rl=None):
            ex=[ll]+[col(leader,k) for k in ("ath_win","course_win","motor_win")]+[col(base,k)-col(leader,k) for k in ("ath_win","motor_win")]
            if runner is not None: ex += [rl]+[col(runner,k) for k in ("ath_win","course_win","motor_win")]+[col(base,k)-col(runner,k) for k in ("ath_win","motor_win")]
            return np.concatenate([base,np.asarray(ex,float)])
        z2=np.stack([ctx(X[BB[j]],X[AA[j]],AA[j]+1) for j in range(30)]); raw=self.second.predict_proba(z2)[:,1]
        p2=np.zeros((6,6))
        for a in range(6):
            js=np.where(AA==a)[0]; v=np.maximum(raw[js],1e-8); v/=v.sum()
            for j,q in zip(js,v): p2[a,BB[j]]=q
        z3=np.stack([ctx(X[C[j]],X[A[j]],A[j]+1,X[B[j]],B[j]+1) for j in range(120)]); rr=self.third.predict_proba(z3)[:,1]
        p3=np.zeros(120)
        for a in range(6):
            for b in range(6):
                js=np.where((A==a)&(B==b))[0]
                if len(js):
                    v=np.maximum(rr[js],1e-8); p3[js]=v/v.sum()
        return p2,p3

    def predict(self,race,date,stage="morning"):
        X,racers=self._matrix(race,date); entry=any(int(r.get("entry_course") or r["lane"])!=int(r["lane"]) for r in racers)
        p1=self._first(X,racers,entry); p2,p3=self._conditionals(X)
        def centered(v,scale):
            v=np.asarray(v,float); return np.clip((v-v.mean())/scale,-2,2)
        ft2=.5*centered([_num(r.get("nat_2"),0) for r in racers],18)+.5*centered([_num(r.get("local_2"),0) for r in racers],20)+.18*centered(X[:,FEATURES.index("ath_second")],.16)+.18*centered(X[:,FEATURES.index("course_second")],.15)
        ft3=.5*centered([_num(r.get("nat_3"),0) for r in racers],22)+.5*centered([_num(r.get("local_3"),0) for r in racers],23)+.18*centered(X[:,FEATURES.index("ath_third")],.16)+.18*centered(X[:,FEATURES.index("course_third")],.15)
        p2n=p2.copy()
        for a in range(6):
            inds=np.array([j for j in range(6) if j!=a]); score=np.log(np.maximum(p2[a,inds],1e-10))+.09*ft2[inds]+.09*self.hist2_pair[a,inds]
            p2n[a,inds]=_softmax(score)
        z=np.log(np.maximum(p3,1e-12))+.075*ft3[C]+.09*self.hist3; p3n=np.zeros(120)
        for a in range(6):
            for b in range(6):
                js=np.where((A==a)&(B==b))[0]
                if len(js): p3n[js]=_softmax(z[js])
        joint=p1[A]*p2n[A,B]*p3n; joint/=joint.sum()
        ranked=np.argsort(-joint,kind="stable"); normal=ranked[:6].tolist()
        for j in np.argsort(-(joint*np.exp(.25*self.prior_rel)),kind="stable"):
            if int(j) not in normal:normal.append(int(j))
            if len(normal)>=8:break
        cand=normal[:6]
        for _ in range(4):
            used={A[j] for j in cand}; score=np.array([joint[j]*(1.3 if A[j] not in used else 1) for j in range(120)]); score[cand]=-1; cand.append(int(np.argmax(score)))
        for j in cand:
            if j not in normal:normal.append(j)
            if len(normal)>=10:break
        for j in ranked:
            if int(j) not in normal:normal.append(int(j))
            if len(normal)>=10:break
        f=CFG["fly"]; vals=[]
        mapping=("nat_win","local_win","nat_2","local_2","motor_2","motor_3")
        for key,sc in zip(mapping,f["scales"][:6]):
            v=np.array([_num(r.get(key),0) for r in racers]); vals.append(np.clip((v-v.mean())/sc,-2.5,2.5))
        for key,sc in (("ath_win",.18),("course_win",.15),("ath_place",.22)):
            v=X[:,FEATURES.index(key)]; vals.append(np.clip((v-v.mean())/sc,-2.5,2.5))
        vals += [np.array([1,0,0,0,0,0]),np.array([0,1,1,1,1,1])]
        attack=_softmax(.65*np.log(np.maximum(p1,1e-10))+np.stack(vals,axis=1)@np.asarray(f["coefficients"]))
        cond=joint/np.maximum(np.array([joint[A==k].sum() for k in range(6)])[A],1e-12); fj=attack[A]*cond
        nh=set(A[normal]); outside=sorted([k for k in range(6) if k not in nh],key=lambda k:-attack[k]); fly=[]
        for head,count in zip(outside,(6,4)):
            fly += sorted(np.where(A==head)[0],key=lambda j:-fj[j])[:count]
        for j in sorted(np.flatnonzero(np.isin(A,outside)),key=lambda j:-fj[j]):
            if len(fly)>=10:break
            if int(j) not in fly:fly.append(int(j))
        win=np.array([joint[A==k].sum() for k in range(6)]); second=np.array([joint[B==k].sum() for k in range(6)]); third=np.array([joint[C==k].sum() for k in range(6)])
        def ticket(j): return {"combination":"-".join(str(x+1) for x in PERMS[j]),"probability":round(float(joint[j]*100),8)}
        nt=[ticket(j) for j in normal[:10]]; ft=[{"combination":"-".join(str(x+1) for x in PERMS[j]),"probability":round(float(fj[j]*100),8)} for j in fly[:10]]
        flyrate=round(float((1-win[0])*100),10)
        pct=lambda values:{str(i+1):round(float(x*100),10) for i,x in enumerate(values)}
        return {"modelVersion":CFG["modelVersion"],"predictionStage":stage,"win":pct(win),"second":pct(second),"third":pct(third),"lane1FlyRate":flyrate,"lane1FlyProbability":flyrate,"lane1Fly":bool(1-win[0]>=.5),"sab":"対象外","ai":nt[:6],"balance":nt[6:8],"aiUpset":nt[8:10],"tickets":nt,"oddsUsed":False,"flyPrediction":{"status":stage,"modelVersion":f["modelVersion"],"allocation":[6,4],"overlapPolicy":f["overlapPolicy"],"probability":flyrate,"isFly":bool(1-win[0]>=.5),"tickets":ft,"oddsUsed":False}}
