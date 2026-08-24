from __future__ import annotations

import csv
import itertools
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

LANES = (1, 2, 3, 4, 5, 6)


def f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "": return default
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def i(v: Any, default: int = 0) -> int:
    try: return int(float(v))
    except (TypeError, ValueError): return default


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def rel_weight(v: Any) -> float:
    s = str(v or "").strip().upper()
    if s == "A": return 1.0
    if s == "B": return 0.78
    if s == "C": return 0.52
    if "参考" in s: return 0.28
    return clamp(f(v, 0.45), 0.20, 1.0)


def norm(values: Mapping[int, float]) -> dict[int, float]:
    x = {k: max(0.01, f(values.get(k), 0.01)) for k in LANES}
    total = sum(x.values()) or 1.0
    out = {k: round(x[k] * 100.0 / total, 4) for k in LANES}
    # exact-enough sum, final adapter rounds to 2 decimals and repairs remainder.
    return out


def rank_low(values: Mapping[int, float]) -> dict[int, int]:
    ordered = sorted(values, key=lambda k: (values[k], k))
    return {k: p + 1 for p, k in enumerate(ordered)}


class ShimonosekiV61Core:
    """Shimonoseki v6.1 deterministic prediction core.

    Design constraints:
    - no odds/result access
    - actual-course remap at final stage
    - all corrections operate on 1st/2nd/3rd probabilities then renormalize
    - recorded kimarite=0 is not treated as latent attack=0
    - trifecta tickets use scenario-conditioned linkage rather than independent marginals only
    """

    def __init__(self, master_dir: str | Path):
        self.master_dir = Path(master_dir)
        self.course_remaining = self._index("shimonoseki_course_remaining_master_v1.csv", lambda r: i(r.get("lane")))
        self.player_course = self._index("shimonoseki_player_course_db_v6_1.csv", lambda r: (i(r.get("player_id")), i(r.get("entry_course"))))
        self.player_weak = self._index("shimonoseki_player_course_weakness_v6_1.csv", lambda r: (i(r.get("player_id")), i(r.get("entry_course"))))
        self.entry_shift = self._index("shimonoseki_player_entry_shift_db_v6_1.csv", lambda r: (i(r.get("player_id")), i(r.get("lane")), i(r.get("entry_course"))))
        self.player_lane = self._index("shimonoseki_player_lane_db_v6_1.csv", lambda r: (i(r.get("player_id")), i(r.get("lane"))))
        self.player_st = self._index("shimonoseki_player_st_course_db_v6_1.csv", lambda r: (i(r.get("player_id")), i(r.get("entry_course"))))
        self.player_summary = self._index("shimonoseki_player_course_summary_v6_1.csv", lambda r: i(r.get("player_id")))
        self.player_type = self._index("shimonoseki_player_type_master.csv", lambda r: i(r.get("player_id_std")))
        self.player_type_lane = self._index("shimonoseki_player_type_master_by_lane.csv", lambda r: (i(r.get("player_id_std")), i(r.get("lane_std"))))
        self.motor = self._index("shimonoseki_motor_type_master_v1.csv", lambda r: i(r.get("motor_no")))
        self.tide = self._rows("shimonoseki_tide_time_course_master_v1.csv")
        self._motor_means = self._means(self.motor.values(), ["win_rate", "top2_rate", "top3_rate", "deashi_score", "nobi_score", "mawariashi_score"])

    def _rows(self, name: str) -> list[dict[str, str]]:
        p = self.master_dir / name
        if not p.is_file(): return []
        with p.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    def _index(self, name: str, keyfn) -> dict[Any, dict[str, str]]:
        out = {}
        for r in self._rows(name):
            k = keyfn(r)
            if k not in (0, (0, 0), (0, 0, 0)): out[k] = r
        return out

    @staticmethod
    def _means(rows, keys):
        out = {}
        rows = list(rows)
        for key in keys:
            vals = [f(r.get(key), math.nan) for r in rows]
            vals = [v for v in vals if math.isfinite(v)]
            out[key] = sum(vals)/len(vals) if vals else 0.0
        return out

    @staticmethod
    def _pid(racer: Mapping[str, Any]) -> int:
        return i(racer.get("player_id") or racer.get("reg_no") or racer.get("registration_number"))

    @staticmethod
    def _racers(race: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        return {i(r.get("lane")): r for r in (race.get("racers") or []) if i(r.get("lane")) in LANES}

    def _pc(self, pid: int, course: int) -> Mapping[str, Any]:
        return self.player_course.get((pid, course)) or self.player_weak.get((pid, course)) or {}

    def _st(self, pid: int, course: int) -> Mapping[str, Any]:
        return self.player_st.get((pid, course)) or {}

    def _type(self, pid: int, course: int) -> Mapping[str, Any]:
        return self.player_type_lane.get((pid, course)) or self.player_type.get(pid) or {}

    def base_actual_course(self, race: Mapping[str, Any], actual: Mapping[int, int]):
        racers = self._racers(race)
        raw = {"first": {}, "second": {}, "third": {}}
        meta = {}
        for lane in LANES:
            racer = racers.get(lane, {})
            pid = self._pid(racer)
            c = i(actual.get(lane), lane) or lane
            venue = self.course_remaining.get(c, {})
            pc = self._pc(pid, c)
            stc = self._st(pid, c)
            tl = self._type(pid, c)
            pl = self.player_lane.get((pid, lane), {})
            starts = f(pc.get("starts"), 0)
            rw = rel_weight(pc.get("reliability"))
            empirical = clamp(starts / (starts + 14.0), 0, .80) * rw
            # course baseline is the stabilizing prior; player-course replaces it only with sufficient evidence.
            base = {
                "first": f(venue.get("win_rate"), 1/6) * 100,
                "second": f(venue.get("second_rate"), 1/6) * 100,
                "third": f(venue.get("third_rate"), 1/6) * 100,
            }
            player = {
                "first": f(pc.get("win_rate"), base["first"]),
                "second": f(pc.get("second_rate"), base["second"]),
                "third": f(pc.get("third_rate"), base["third"]),
            }
            # lane-type scores are intentionally low weight; they carry style without overwhelming actual course DB.
            type_score = {
                "first": f(tl.get("lane_win_score"), f(tl.get("shimo_win_rate"), 0.0)) * 100,
                "second": f(tl.get("shimo_second_rate"), 0.0) * 100,
                "third": f(tl.get("shimo_third_rate"), 0.0) * 100,
            }
            type_rel = clamp(f(tl.get("sample_reliability"), 0.0), 0, 1)
            type_w = .12 * type_rel
            lane_starts = f(pl.get("starts"), 0)
            lane_w = min(.08, lane_starts / 250.0)
            lane_vals = {"first": f(pl.get("win_rate"), 0), "second": f(pl.get("second_rate"), 0), "third": f(pl.get("third_rate"), 0)}
            for pos in raw:
                val = base[pos]*(1-empirical) + player[pos]*empirical
                if type_score[pos] > 0: val = val*(1-type_w) + type_score[pos]*type_w
                if lane_vals[pos] > 0: val = val*(1-lane_w) + lane_vals[pos]*lane_w
                # national/local strength only tiny tie-breakers.
                if pos == "first": val *= 1 + clamp((f(racer.get("local_win"), 5.0)-5.0)*.012, -.05, .05)
                raw[pos][lane] = max(.05, val)
            meta[lane] = {"player_id": pid, "actual_course": c, "player_course": c, "st_course": c, "type_course": c, "st_starts": f(stc.get("starts"), 0), "course_starts": starts, "course_reliability": pc.get("reliability"), "course_strength": pc.get("course_strength")}
        return {p: norm(raw[p]) for p in raw}, meta

    def motor_base(self, probs, race):
        racers = self._racers(race); meta={}
        out={p:dict(probs[p]) for p in probs}
        for lane in LANES:
            r=racers.get(lane,{})
            m=i(r.get("motor_no")); row=self.motor.get(m,{})
            if not row: continue
            rel=clamp(f(row.get("sample_reliability"),.5),0,1)
            winz=(f(row.get("win_rate"),self._motor_means["win_rate"])-self._motor_means["win_rate"])*100
            top2z=(f(row.get("top2_rate"),self._motor_means["top2_rate"])-self._motor_means["top2_rate"])*100
            top3z=(f(row.get("top3_rate"),self._motor_means["top3_rate"])-self._motor_means["top3_rate"])*100
            recent=r.get("motor_recent") or {}
            trend={"up":1.0,"down":-1.0}.get(str(recent.get("trend")),0.0)
            delta={"first":clamp(winz*.10*rel + trend*.8,-2.2,2.2),"second":clamp(top2z*.07*rel+trend*.5,-1.7,1.7),"third":clamp(top3z*.06*rel+trend*.45,-1.5,1.5)}
            for p,d in delta.items(): out[p][lane]+=d
            meta[lane]={"motor_no":m,"delta":delta,"type":row.get("motor_type")}
        return {p:norm(out[p]) for p in out},meta

    def series(self, probs, race):
        out={p:dict(probs[p]) for p in probs}; meta={}
        for lane,r in self._racers(race).items():
            runs=r.get("season_runs") or []
            finishes=[]; sts=[]
            for x in runs[-6:]:
                s=str(x.get("finish") or ""); digits="".join(ch for ch in s if ch.isdigit())
                if digits: finishes.append(i(digits))
                st=f(x.get("st"),math.nan)
                if math.isfinite(st): sts.append(st)
            if not finishes: continue
            avg=sum(finishes)/len(finishes); top3=sum(x<=3 for x in finishes)/len(finishes)
            d1=clamp((3.5-avg)*.55 + (top3-.5)*1.2,-2,2)
            d2=clamp((top3-.5)*1.1,-1.2,1.2); d3=clamp((top3-.5)*.9,-1,1)
            if sts and sum(sts)/len(sts)<=.14: d1+=.35; d2+=.25
            out["first"][lane]+=d1; out["second"][lane]+=d2; out["third"][lane]+=d3
            meta[lane]={"avg_finish":round(avg,3),"top3":round(top3,3),"delta":[round(d1,3),round(d2,3),round(d3,3)]}
        return {p:norm(out[p]) for p in out},meta

    def water_tide(self, probs, race, actual, direct=None, tide_events=None):
        # Conservative by design. Apply a small course-conditioned delta only when race payload supplies phase/bucket.
        phase=str(race.get("tide_phase") or race.get("tidePhase") or "").lower()
        bucket=str(race.get("tide_phase_bucket") or race.get("tidePhaseBucket") or "").lower()
        time_band=str(race.get("time_band") or race.get("timeBand") or "").lower()
        candidates=[]
        for row in self.tide:
            if phase and str(row.get("tide_phase","")).lower()!=phase: continue
            if bucket and str(row.get("tide_phase_bucket","")).lower()!=bucket: continue
            if time_band and str(row.get("time_band","")).lower() not in (time_band,"unknown"): continue
            candidates.append(row)
        if not candidates: return probs,{"applied":False}
        byc={i(r.get("lane")):r for r in candidates}
        out={p:dict(probs[p]) for p in probs}
        for lane in LANES:
            c=i(actual.get(lane),lane); row=byc.get(c); base=self.course_remaining.get(c,{})
            if not row: continue
            for p,k in (("first","win_rate"),("second","second_rate"),("third","third_rate")):
                d=(f(row.get(k))-f(base.get(k)))*100
                out[p][lane]+=clamp(d*.12,-1.3,1.3)
        return {p:norm(out[p]) for p in out},{"applied":True,"phase":phase,"bucket":bucket,"time_band":time_band}

    @staticmethod
    def actual_courses(exhibition: Mapping[str, Any]) -> dict[int,int]:
        out={lane:lane for lane in LANES}
        for r in (exhibition.get("data") or {}).get("entries",[]):
            lane=i(r.get("lane")); c=i(r.get("exhibition_course"),lane)
            if lane in LANES and c in LANES: out[lane]=c
        return out

    def live_layers(self, probs, race, actual, direct, exhibition, original):
        out={p:dict(probs[p]) for p in probs}
        exrows={i(r.get("lane")):r for r in (exhibition.get("data") or {}).get("entries",[])}
        orows={i(r.get("lane")):r for r in (original.get("data") or {}).get("entries",[])}
        exvals={l:f(exrows.get(l,{}).get("exhibition_time"),99) for l in LANES}; exrank=rank_low(exvals)
        sumvals={}
        sum_missing=set()
        for lane in LANES:
            value=f(orows.get(lane,{}).get("sum"),math.nan)
            if math.isfinite(value): sumvals[lane]=value
            else: sum_missing.add(lane)
        sumrank=rank_low(sumvals) if sumvals else {}
        for lane in sum_missing: sumrank[lane]=3.5
        sum_status={lane:("missing" if lane in sum_missing else "ranked") for lane in LANES}
        st={l:f(exrows.get(l,{}).get("start_time"),.30) for l in LANES}
        # exhibition time + original SUM are final micro-corrections, not a base replacement.
        meta={}
        for lane in LANES:
            er=3.5-exrank[lane]; sr=3.5-sumrank[lane]
            c=i(actual.get(lane),lane)
            st_support=clamp((.16-st[lane])*7.5,-.75,.75)
            # ST weight is lower for lane/course shifts.
            shift_factor=.65 if c!=lane else 1.0
            d1=clamp(er*.30 + sr*.18 + st_support*.32*shift_factor,-1.8,1.8)
            d2=clamp(er*.24 + sr*.15 + st_support*.28*shift_factor,-1.45,1.45)
            d3=clamp(er*.20 + sr*.14 + st_support*.24*shift_factor,-1.2,1.2)
            out["first"][lane]+=d1; out["second"][lane]+=d2; out["third"][lane]+=d3
            meta[lane]={"course":c,"st":st[lane],"exhibition_rank":exrank[lane],"sum_rank":"missing" if lane in sum_missing else sumrank[lane],"sum_status":sum_status[lane],"sum_adjustment":0 if lane in sum_missing else round(sr,3),"sum_delta":[round(sr*.18,3),round(sr*.15,3),round(sr*.14,3)],"delta":[round(d1,3),round(d2,3),round(d3,3)]}
        return {p:norm(out[p]) for p in out}, {"boats":meta,"st":st,"ex_rank":exrank,"sum_rank":sumrank,"sum_rank_status":sum_status}

    def latent_attack(self, race, actual, live_meta):
        racers=self._racers(race); st=live_meta.get("st",{}); exrank=live_meta.get("ex_rank",{}); sumrank=live_meta.get("sum_rank",{})
        out={}
        for lane in LANES:
            c=i(actual.get(lane),lane)
            if c<2 or c>6: continue
            r=racers.get(lane,{}); pid=self._pid(r); pc=self._pc(pid,c); stc=self._st(pid,c); typ=self._type(pid,c); venue=self.course_remaining.get(c,{})
            starts=f(pc.get("starts"),0); rel=rel_weight(pc.get("reliability")); shrink=clamp(starts/(starts+12)*rel,.10,.78)
            observed=max(f(typ.get("shimo_sashi_rate"),0),f(typ.get("shimo_makuri_rate"),0),f(typ.get("shimo_makurisashi_rate"),0),f(typ.get("all_sashi_rate"),0),f(typ.get("all_makuri_rate"),0),f(typ.get("all_makurisashi_rate"),0))
            pc_win=f(pc.get("win_rate"),f(venue.get("win_rate"))*100)/100
            pc_top3=f(pc.get("top3_rate"),f(venue.get("top3_rate"))*100)/100
            vwin=f(venue.get("win_rate")); vtop3=f(venue.get("top3_rate"))
            course_cap=clamp(.5 + (pc_win-vwin)*1.7 + (pc_top3-vtop3)*.75,0,1)
            avgst=f(stc.get("avg_st"),f(pc.get("avg_st"),f(typ.get("shimo_avg_st"),.18))); topst=f(stc.get("top_st_rate"),f(pc.get("top_st_rate"),0))/100
            st_hist=clamp((.20-avgst)/.11*.65 + topst*.35,0,1)
            st_live=clamp((.20-f(st.get(lane),.20))/.16,0,1)
            live_quality=clamp(((6-f(exrank.get(lane),6))/5)*.55 + ((6-f(sumrank.get(lane),3.5))/5)*.45,0,1)
            type_attack=clamp(f(typ.get("shimo_st_attack_score"),f(typ.get("all_st_attack_score"),.45)),0,1)
            latent=clamp(course_cap*.28 + st_hist*.18 + st_live*.18 + live_quality*.16 + type_attack*.20,0,1)
            # observed kimarite is evidence but never a gate.
            score=clamp((observed*.32 + latent*.68)*(0.55+0.45*shrink),0,1)
            if c==6:
                score*=.72
            out[lane]={"course":c,"player_course":c,"st_course":c,"type_course":c,"observed":round(observed,4),"latent":round(latent,4),"score":round(score,4),"shrink":round(shrink,4)}
        return out

    def escape_attack_interaction(self, probs, race, actual, live_meta, attack):
        out={p:dict(probs[p]) for p in probs}; racers=self._racers(race)
        inside=next((l for l in LANES if i(actual.get(l),l)==1),None)
        if inside is None: return probs,{"applied":False,"attack":attack}
        r=racers.get(inside,{}); pid=self._pid(r); pc=self._pc(pid,1); typ=self._type(pid,1); venue=self.course_remaining.get(1,{})
        starts=f(pc.get("starts"),0); rel=rel_weight(pc.get("reliability")); shrink=clamp(starts/(starts+12)*rel,.12,.82)
        pcwin=f(pc.get("win_rate"),f(venue.get("win_rate"))*100)/100
        escape=f(typ.get("shimo_escape_rate"),f(typ.get("all_escape_rate"),0))
        # generated escape may be zero; use course win as fallback rather than treating zero as weak by itself.
        defense=clamp(pcwin*.72 + (escape if escape>0 else pcwin)*.18 + clamp((.20-f(pc.get("avg_st"),.17))/.12,0,1)*.10,0,1)
        defense=defense*shrink + f(venue.get("win_rate"),.589)*(1-shrink)
        vulnerability=clamp((.62-defense)/.42,0,1)
        candidates={l:d["score"] for l,d in attack.items() if d["course"] in (2,3,4,5)}
        if not candidates: return probs,{"applied":False,"inside":inside,"defense":defense,"attack":attack}
        # only transfer when a credible attack aligns with actual vulnerability.
        best=max(candidates.values()); combined=vulnerability*best
        total_move=clamp((combined-.08)*9.0,0,7.5)
        if total_move<=.05: return probs,{"applied":False,"inside":inside,"defense":round(defense,4),"vulnerability":round(vulnerability,4),"move":0,"attack":attack}
        weights={l:max(.01,s**1.8) for l,s in candidates.items()}; ws=sum(weights.values())
        out["first"][inside]-=total_move
        for l,w in weights.items(): out["first"][l]+=total_move*w/ws
        # attack pressure mainly changes residual ordering, smaller than first-place transfer.
        for l,w in weights.items():
            add=total_move*w/ws
            out["second"][l]+=add*.45; out["third"][l]+=add*.28
        return {p:norm(out[p]) for p in out},{"applied":True,"inside":inside,"defense":round(defense,4),"vulnerability":round(vulnerability,4),"move":round(total_move,3),"attack":attack}

    def predict_final(self, race, exhibition, original, direct=None, tide_events=None):
        if "result" in race or "odds" in race:
            # Ignore, never consume. Explicit trace is useful in production audit.
            pass
        actual=self.actual_courses(exhibition)
        probs,course=self.base_actual_course(race,actual)
        probs,motor=self.motor_base(probs,race)
        probs,series=self.series(probs,race)
        probs,water=self.water_tide(probs,race,actual,direct,tide_events)
        probs,live=self.live_layers(probs,race,actual,direct or {},exhibition,original)
        attack=self.latent_attack(race,actual,live)
        probs,escape=self.escape_attack_interaction(probs,race,actual,live,attack)
        maps=self.maps(probs)
        debug={"actual_course":actual,"course_remap":course,"motor":motor,"series":series,"water":water,"live":live,"latent_attack":attack,"escape_attack":escape,"result_used":False,"odds_used":False,"calibration_mode":"identity_pending_chronological_fit"}
        tickets=self.build_tickets(maps,race,debug)
        sab,smeta=self.sab_score(maps,tickets,debug)
        debug["sab"]=smeta
        return {"phase":"final","status":"complete","probabilities":maps,**maps,"sab":sab,"tickets":tickets,"ai":[x["combo"] for x in tickets["main"]],"balance":[x["combo"] for x in tickets["deviation"]],"aiUpset":[x["combo"] for x in tickets["upset"]],"debug":debug}

    @staticmethod
    def maps(probs):
        out={}
        for outk,ink in (("win","first"),("second","second"),("third","third")):
            vals={l:round(probs[ink][l],2) for l in LANES}; diff=round(100-sum(vals.values()),2); best=max(vals,key=vals.get); vals[best]=round(vals[best]+diff,2)
            out[outk]={str(l):vals[l] for l in LANES}
        return out

    def scenario_context(self, race, debug):
        actual=debug.get("actual_course",{}); attack=debug.get("latent_attack",{}); live=debug.get("live",{}); st=live.get("st",{}); exr=live.get("ex_rank",{}); sr=live.get("sum_rank",{})
        lane_by_course={i(c):i(l) for l,c in actual.items()}
        attack_scores={l:f(v.get("score")) for l,v in attack.items()}
        # Residual attack score is allowed to react strongly to the same-day slit for 2nd/3rd linkage.
        # It does NOT directly become a win-probability transfer. This separation is essential for
        # races where a weak historical c4 can still survive 2nd after a sharp corner attack.
        residual_attack={}
        for l in attack_scores:
            co=i(actual.get(l),l)
            st_live=clamp((.18-f(st.get(l),.18))/.16,0,1)
            exq=(6-f(exr.get(l),6))/5
            sumq=(6-f(sr.get(l),3.5))/5
            live_residual=.70*st_live+.20*exq+.10*sumq
            residual_attack[l]=clamp(max(attack_scores[l],live_residual),0,1) if co in (3,4,5) else attack_scores[l]
        primary=max((l for l in residual_attack if i(actual.get(l)) in (3,4,5)), key=lambda l:residual_attack[l], default=None)
        outer=lane_by_course.get(6)
        # outer residual is strong when it is early at slit. A clearly fastest c6 receives
        # an extra 'outer lead' term; this distinguishes R4-type c6 survival from R7-type c4 attack.
        outer_score=0.0; outer_lead=0.0
        if outer:
            outer_score=clamp((.20-f(st.get(outer),.20))/.18*.60 + ((6-f(exr.get(outer),6))/5)*.20 + ((6-f(sr.get(outer),3.5))/5)*.20,0,1)
            others=sorted(f(st.get(l),.30) for l in LANES if l!=outer)
            next_st=others[0] if others else .30
            outer_lead=clamp((next_st-f(st.get(outer),.30)-.02)/.07,0,1)
        return {"lane_by_course":lane_by_course,"primary_attacker":primary,"attack":attack_scores,"residual_attack":residual_attack,"outer_lane":outer,"outer_score":outer_score,"outer_lead":outer_lead,"st":st,"ex_rank":exr,"sum_rank":sr}

    def ticket_score(self, a,b,c,maps,ctx):
        p1=f(maps["win"].get(str(a))); p2=f(maps["second"].get(str(b))); p3=f(maps["third"].get(str(c)))
        base=max(.0001,p1*p2*p3)
        actual_by_lane={l:co for co,l in ctx["lane_by_course"].items()}
        ca=actual_by_lane.get(a,a); cb=actual_by_lane.get(b,b); cc=actual_by_lane.get(c,c)
        mult=1.0; reasons=[]
        if ca==1:
            # 1逃げ conditional second.
            if cb==2:
                # penalize a clearly late c2 slit; otherwise classic sashi-nokori.
                gap=f(ctx["st"].get(b),.20)-f(ctx["st"].get(a),.20)
                m=1.16 if gap<.08 else .88
                mult*=m; reasons.append("1escape_c2_residual")
            if cb in (3,4,5):
                atk=f(ctx.get("residual_attack",{}).get(b),f(ctx["attack"].get(b))); mult*=1+1.18*atk; reasons.append(f"c{cb}_attack_residual")
            if cb==6:
                mult*=1+1.30*f(ctx["outer_score"])+1.20*f(ctx.get("outer_lead")); reasons.append("c6_outer_residual")
            # third-place link conditional on second-place path.
            if cb==4 and cc in (5,6):
                mult*=1.75 if cc==6 else 1.45; reasons.append("c4_to_outer_follow")
            elif cb==5 and cc==6:
                mult*=1.55; reasons.append("c5_to_c6_follow")
            elif cb==3 and cc in (4,5,6):
                mult*=1.28 + .32*f(ctx["outer_score"] if cc==6 else ctx["attack"].get(c)); reasons.append("c3_attack_follow")
            elif cb==6 and cc in (3,4,5):
                # when c6 survives, prefer a centre boat with good final live quality.
                q=((6-f(ctx["ex_rank"].get(c),6))/5*.45 + (6-f(ctx["sum_rank"].get(c),3.5))/5*.55)
                mult*=1.20+.85*q; reasons.append("c6_then_center_quality")
            # center attacker did not win: inside/centre residual are still valid, but not dominant.
        else:
            # Upset scenarios: attacker head often leaves inside residual second.
            if ca in (2,3,4,5) and cb==1:
                mult*=1.45; reasons.append("attacker_head_inside_residual")
                if ca==4 and cc in (5,6): mult*=1.35
                if ca==3 and cc in (4,5): mult*=1.20
            if ca==6 and cb==1: mult*=1.18
        # live quality only breaks ties; never dominates ticket score by itself.
        q2=(6-f(ctx["ex_rank"].get(b),6))/5; q3=(6-f(ctx["sum_rank"].get(c),3.5))/5
        mult*=.92 + .11*q2 + .10*q3
        return base*mult, reasons

    def build_tickets(self,maps,race,debug):
        ctx=self.scenario_context(race,debug)
        scored=[]
        for a,b,c in itertools.permutations(LANES,3):
            s,reasons=self.ticket_score(a,b,c,maps,ctx)
            scored.append((s,a,b,c,reasons))
        scored.sort(reverse=True,key=lambda x:(x[0],-x[1],-x[2],-x[3]))
        main=[]; dev=[]; upset=[]; used=set(); top_head=int(scored[0][1])
        def add(dst,row):
            _,a,b,c,reasons=row; combo=f"{a}-{b}-{c}"
            if combo in used:return False
            used.add(combo); dst.append({"combo":combo,"score":round(row[0],5),"scenario":reasons}); return True
        for row in scored:
            if len(main)>=6: break
            if row[1]==top_head: add(main,row)
        for row in scored:
            if len(dev)>=2: break
            if row[1]==top_head and f"{row[1]}-{row[2]}-{row[3]}" not in used: add(dev,row)
        for row in scored:
            if len(upset)>=2: break
            if row[1]!=top_head: add(upset,row)
        return {"main":main,"deviation":dev,"upset":upset,"context":ctx}

    def sab_score(self,maps,tickets,debug):
        wins=sorted((f(maps["win"][str(l)]),l) for l in LANES)[::-1]
        p1,l1=wins[0]; p2=wins[1][0]; margin=p1-p2
        probs=[max(.0001,f(maps["win"][str(l)])/100) for l in LANES]
        entropy=-sum(p*math.log(p) for p in probs)/math.log(6)
        viable=sum(p>=.12 for p in probs)
        attack=debug.get("latent_attack",{}); credible=sum(f(x.get("score"))>=.34 for x in attack.values())
        move=f((debug.get("escape_attack") or {}).get("move"),0)
        # Lower entropy/more separation = higher confidence. Attack branching/large final transfer = lower reproducibility.
        score=36 + clamp((p1-35)*.65,0,22) + clamp(margin*.55,0,18) + (1-entropy)*22
        score-=max(0,viable-2)*5.5 + max(0,credible-1)*3.0 + min(8,move*.8)
        if debug.get("water",{}).get("rough"): score-=4
        score=clamp(score,0,100)
        if score>=78 and viable<=2 and credible<=1: grade="S"
        elif score>=62: grade="A"
        elif score>=47: grade="B"
        else: grade="見"
        return grade,{"score":round(score,2),"top_win":round(p1,2),"margin":round(margin,2),"entropy":round(entropy,4),"viable_heads":viable,"credible_attackers":credible,"escape_attack_move":round(move,2)}
