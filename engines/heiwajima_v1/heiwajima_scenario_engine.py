SCENARIOS = {
 'S01_1_ESCAPE': {'name':'1逃げ','head':[1],'second':[2,3,4],'third':[2,3,4,5,6]},
 'S02_1_REMAINS': {'name':'外攻め時の1残り','head':[2,3,4,5],'second':[1],'third':[1,2,3,4,5,6]},
 'S03_2_SASHI': {'name':'2差し頭','head':[2],'second':[1,3,4],'third':[1,3,4,5,6]},
 'S04_2_SASHI_1_REMAINS': {'name':'2差しから1残し','head':[2],'second':[1],'third':[3,4,5,6]},
 'S05_3_MAKURI': {'name':'3まくり','head':[3],'second':[1,2,4,5,6],'third':[1,2,4,5,6]},
 'S06_3_MAKURIZASHI': {'name':'3まくり差し','head':[3],'second':[1,2,4],'third':[1,2,4,5,6]},
 'S07_3_ATTACK_LINK': {'name':'3攻めから4・5・6連動','head':[3],'second':[4,5,6,1,2],'third':[4,5,6,1,2]},
 'S08_4_KADO': {'name':'4カドまくり','head':[4],'second':[1,2,3,5,6],'third':[1,2,3,5,6]},
 'S09_4_ATTACK_LINK': {'name':'4攻めから5・6連動','head':[4],'second':[5,6,1,2,3],'third':[5,6,1,2,3]},
 'S10_1_3_BATTLE': {'name':'1と3の競りから4・5差し','head':[4,5],'second':[1,2,3,4,5],'third':[1,2,3,4,5,6]},
 'S11_WALL_FAILURE': {'name':'壁役不成立','head':[2,3,4,5],'second':[1,2,3,4,5,6],'third':[1,2,3,4,5,6]},
 'S12_INSIDE_COLLAPSE': {'name':'内崩れ','head':[3,4,5,6],'second':[3,4,5,6,1,2],'third':[1,2,3,4,5,6]},
 'S13_OUTER_THIRD': {'name':'外枠3着残り','head':[1,2,3,4],'second':[1,2,3,4,5],'third':[5,6]},
 'S14_ENTRY_CHANGE': {'name':'進入変更による展開変化','head':[1,2,3,4,5,6],'second':[1,2,3,4,5,6],'third':[1,2,3,4,5,6]}
}

def evaluate_scenarios(boats, water):
    scores={k:0.05 for k in SCENARIOS}
    by_course={int(b['actual_course']):b for b in boats}
    one=by_course.get(1,{}); two=by_course.get(2,{}); three=by_course.get(3,{}); four=by_course.get(4,{})
    scores['S01_1_ESCAPE'] += max(0,float(one.get('win_prob',0))-0.25)
    scores['S03_2_SASHI'] += max(0,float(two.get('win_prob',0))-0.10)
    scores['S05_3_MAKURI'] += max(0,float(three.get('win_prob',0))-0.10)
    scores['S08_4_KADO'] += max(0,float(four.get('win_prob',0))-0.10)
    if any(b.get('entry_changed') for b in boats): scores['S14_ENTRY_CHANGE'] += .35
    if water.get('outer_bias',0)>0: scores['S13_OUTER_THIRD'] += min(.25,water['outer_bias'])
    if water.get('center_bias',0)>0: scores['S07_3_ATTACK_LINK'] += min(.2,water['center_bias']); scores['S09_4_ATTACK_LINK'] += min(.2,water['center_bias'])
    total=sum(scores.values())
    return sorted([{'id':k,'name':SCENARIOS[k]['name'],'probability':v/total} for k,v in scores.items()],key=lambda x:x['probability'],reverse=True)
