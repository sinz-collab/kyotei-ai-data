SCENARIOS={
 "S01_1_ESCAPE":{"name":"1逃げ","head":[1],"second":[2,3,4],"third":[2,3,4,5,6]},
 "S03_2_SASHI":{"name":"2差し頭","head":[2],"second":[1,3,4],"third":[1,3,4,5,6]},
 "S05_3_MAKURI":{"name":"3まくり","head":[3],"second":[4,5,1,6],"third":[1,2,4,5,6]},
 "S06_3_MAKURIZASHI":{"name":"3まくり差し","head":[3],"second":[1,4,2],"third":[1,2,4,5,6]},
 "S07_3_ATTACK_LINK":{"name":"3攻めから4・5・6連動","head":[3],"second":[4,5,6,1],"third":[4,5,6,1,2]},
 "S08_4_KADO":{"name":"4カド攻め","head":[4],"second":[5,6,1,2,3],"third":[1,2,3,5,6]},
 "S09_4_ATTACK_LINK":{"name":"4攻めから5・6連動","head":[4],"second":[5,6,1],"third":[5,6,1,2,3]},
 "S10_1_3_BATTLE":{"name":"1と3の競りから4・5差し","head":[4,5],"second":[1,3,4,5],"third":[1,2,3,4,5,6]},
 "S11_WALL_FAILURE":{"name":"壁役不成立","head":[2,3,4,5],"second":[1,3,4,5,6],"third":[1,2,3,4,5,6]},
 "S12_INSIDE_COLLAPSE":{"name":"内崩れ","head":[3,4,5,6],"second":[3,4,5,6,1],"third":[1,2,3,4,5,6]},
 "S13_OUTER_THIRD":{"name":"外枠3着連動","head":[1,2,3,4],"second":[1,2,3,4,5],"third":[5,6]},
 "S14_ENTRY_CHANGE":{"name":"進入変更による展開変化","head":[1,2,3,4,5,6],"second":[1,2,3,4,5,6],"third":[1,2,3,4,5,6]}
}

def _clip(x,lo=0,hi=1): return max(lo,min(hi,float(x)))
def evaluate_scenarios(boats, water):
    byc={int(b["actual_course"]):b for b in boats}
    p=lambda c,k="win_prob": float(byc.get(c,{}).get(k,0))
    scores={k:.025 for k in SCENARIOS}
    # 逃げは1着率だけでなく壁・センター攻め圧・水面で評価
    wall=(p(2)+p(3))*0.5
    attack3=p(3)+max(0,water.get("center_bias",0))*0.8
    attack4=p(4)+max(0,water.get("center_bias",0))*0.7
    escape=p(1)+water.get("escape_bias",0)+max(0,wall-.15)*.30-max(0,attack3-.17)*.45-max(0,attack4-.14)*.25
    scores["S01_1_ESCAPE"] += _clip(escape-.24,0,.38)
    scores["S03_2_SASHI"] += _clip(p(2)-.10,0,.22)
    scores["S05_3_MAKURI"] += _clip(p(3)-.105,0,.24)+max(0,water.get("center_bias",0))*.55
    scores["S06_3_MAKURIZASHI"] += _clip(p(3)-.12,0,.20)+max(0,p(1)-.34)*.12
    scores["S07_3_ATTACK_LINK"] += _clip(p(3)-.11,0,.20)+max(0,water.get("center_bias",0))*.70
    scores["S08_4_KADO"] += _clip(p(4)-.09,0,.23)+max(0,water.get("center_bias",0))*.50
    scores["S09_4_ATTACK_LINK"] += _clip(p(4)-.10,0,.20)+max(0,water.get("outer_bias",0))*.45
    if attack3>.18 and p(1)>.30: scores["S10_1_3_BATTLE"] += .10
    if p(2)<.11: scores["S11_WALL_FAILURE"] += .10
    if p(1)<.34 and water.get("center_bias",0)>0: scores["S12_INSIDE_COLLAPSE"] += .11
    if water.get("outer_bias",0)>0: scores["S13_OUTER_THIRD"] += min(.18,water["outer_bias"]*1.5)
    if any(b.get("entry_changed") for b in boats): scores["S14_ENTRY_CHANGE"] += .30
    total=sum(scores.values()) or 1
    out=[]
    for sid,v in scores.items():
        x=dict(SCENARIOS[sid]); x.update({"id":sid,"probability":v/total}); out.append(x)
    return sorted(out,key=lambda x:x["probability"],reverse=True)

def scenario_position_adjustments(scenarios):
    adj={i:{"win":0.0,"second":0.0,"third":0.0} for i in range(1,7)}
    for s in scenarios[:6]:
        w=float(s["probability"])
        for i in s.get("head",[]): adj[i]["win"] += w
        for i in s.get("second",[]): adj[i]["second"] += w/max(1,len(s["second"]))
        for i in s.get("third",[]): adj[i]["third"] += w/max(1,len(s["third"]))
    return adj
