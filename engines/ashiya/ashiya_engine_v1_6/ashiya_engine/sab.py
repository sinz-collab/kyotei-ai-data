
from __future__ import annotations
import math

def entropy(vals):
    return -sum(v*math.log(max(v,1e-12)) for v in vals)/math.log(len(vals))
def grade(probs,structure,audit):
    head_clarity=max(probs['win'])-sorted(probs['win'],reverse=True)[1]
    head_score=min(1.0,head_clarity/0.22)
    scenario=max((x['probability'] for x in structure['scenarios']),default=0)
    stability=1-(entropy(probs['second'])+entropy(probs['third']))/2
    completeness=audit['model'].get('coverage',0)
    contradictions=0.15 if audit.get('entry_changed') and not audit.get('actual_entry_complete') else 0
    score=.30*head_score+.25*scenario+.20*stability+.15*completeness+.10*(1-contradictions)
    if completeness<0.35: score=min(score,0.61)
    if score>=.78:g='S'
    elif score>=.62:g='A'
    elif score>=.45:g='B'
    else:g='見'
    return {'grade':g,'score':round(score,4),'components':{'head_clarity':round(head_score,4),'scenario_agreement':round(scenario,4),'conditional_stability':round(stability,4),'data_completeness':round(completeness,4),'contradiction_risk':round(contradictions,4)}}
