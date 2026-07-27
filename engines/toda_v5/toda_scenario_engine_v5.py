
from toda_utils_v5 import LANES, num, clamp

def _st(r, profile):
    vals = [num(profile.get("avg_st"), 9), num(r.get("local_st"),9), num(r.get("avg_st"),9)]
    vals = [v for v in vals if v < 1]
    return min(vals) if vals else .19

def detect_scenarios(racers, profiles, base_scores, context):
    by = {int(r["lane"]):r for r in racers}
    s = []
    one = by[1]
    one_profile = profiles["1"]
    one_rel = (num(one.get("local_win"),num(one.get("nat_win"),4.5))-4.8)*.42
    one_rel += clamp((.185-_st(one,one_profile))*8,-.7,.7)
    one_rel += clamp(num(one_profile.get("top3_vs_course_avg"),0)/20,-.8,.8)
    one_weak = one_rel < -.15 or base_scores["1"] < max(base_scores[str(i)] for i in (2,3,4))-.6

    if not one_weak or base_scores["1"] >= max(base_scores[str(i)] for i in (2,3,4))-.25:
        s.append({"id":"IN_ESCAPE","label":"1逃げ","head":1,"weight":clamp(.86+one_rel*.22,.35,1.28),"links":[2,3,4,5,6]})

    r2=by[2]; p2=profiles["2"]
    if _st(r2,p2)<=.185 and (num(p2.get("win_rate"),0)>=12 or str(r2.get("class","")).startswith("A")):
        s.append({"id":"TWO_SASHI","label":"2差し","head":2,"weight":1.12 if one_weak else .72,"links":[1,3,4,5,6]})

    r3=by[3]; p3=profiles["3"]
    if _st(r3,p3)<=.185 and base_scores["3"]>=base_scores["2"]-.65:
        label="3まくり差し" if num(p3.get("second_rate"),0)>num(p3.get("win_rate"),0) else "3攻め"
        s.append({"id":"THREE_ATTACK","label":label,"head":3,"weight":1.18 if one_weak else .80,"links":[4,5,1,2,6]})

    r4=by[4]; p4=profiles["4"]
    if _st(r4,p4)<=.185 and base_scores["4"]>=base_scores["3"]-.65:
        s.append({"id":"FOUR_KADO","label":"4カド攻め","head":4,"weight":1.12 if one_weak else .76,"links":[5,6,1,3,2]})

    # outer head only when evidence is strong
    for lane in (5,6):
        p=profiles[str(lane)]
        if base_scores[str(lane)] >= max(base_scores["2"],base_scores["3"],base_scores["4"])-.25 and num(p.get("win_rate"),0)>=10:
            s.append({"id":f"OUTER_{lane}","label":f"{lane}外攻め","head":lane,"weight":.55,"links":[1,3,4,2,6 if lane==5 else 5]})

    wind=num(context.get("wind_speed"),0); wave=num(context.get("wave_height"),0)
    phase=str(context.get("tide_phase") or "")
    for x in s:
        if wind>=4 or wave>=4:
            if x["head"] in (3,4,5): x["weight"] += .10
            if x["head"]==1: x["weight"] -= .10
        if any(k in phase for k in ("干潮","下げ止まり","低潮")) and x["head"] in (3,4): x["weight"] += .08
        if any(k in phase for k in ("上げ","満潮")) and x["head"]==1: x["weight"] += .06
        x["weight"]=clamp(x["weight"],.20,1.35)
    return sorted(s,key=lambda x:x["weight"],reverse=True), one_weak
