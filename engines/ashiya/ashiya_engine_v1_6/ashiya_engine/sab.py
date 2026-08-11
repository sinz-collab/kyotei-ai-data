
from __future__ import annotations
import math

def entropy(vals):
    return -sum(v*math.log(max(v,1e-12)) for v in vals)/math.log(len(vals))

def agreement(a,b):
    pairs=total=0
    for i in range(len(a)):
        for j in range(i+1,len(a)):
            if a[i]==a[j] or b[i]==b[j]:
                continue
            total+=1
            pairs+=1 if (a[i]-a[j])*(b[i]-b[j])>0 else 0
    return pairs/total if total else 0.5

def grade(probs,structure,audit):
    head_clarity=max(probs['win'])-sorted(probs['win'],reverse=True)[1]
    head_score=min(1.0,head_clarity/0.22)
    scenario=max((x['probability'] for x in structure['scenarios']),default=0)
    top_head=probs['win'].index(max(probs['win']))+1
    scenario_match=1.0 if top_head in ({1,structure.get('attacker')} | {x.get('attacker') for x in structure['scenarios']}) else 0.0
    scenario_agreement=0.55*scenario+0.45*scenario_match
    candidates=structure.get('head_candidates') or []
    candidate_total=sum(float(x.get('scenario_score') or x.get('win') or 0) for x in candidates)
    candidate_focus=(max((float(x.get('scenario_score') or x.get('win') or 0) for x in candidates),default=0)/candidate_total) if candidate_total>0 else 1.0
    scenario_conflict=1-candidate_focus
    scenario_agreement=min(scenario_agreement,candidate_focus)
    conditionals=structure.get('head_conditionals') or {}
    concentration=[]
    for candidate in candidates:
        lane=int(candidate['lane'])
        row=conditionals.get(lane) or conditionals.get(str(lane))
        if row:
            focus=1-(entropy(row['second'])+entropy(row['third']))/2
            concentration.append((float(candidate.get('win') or probs['win'][lane-1]),focus))
    if concentration:
        total=sum(x[0] for x in concentration)
        stability=sum(weight*focus for weight,focus in concentration)/total
    else:
        stability=1-(entropy(probs['second'])+entropy(probs['third']))/2
    completeness=audit['model'].get('coverage',0)
    contradictions=0.15 if audit.get('entry_changed') and not audit.get('actual_entry_complete') else 0
    day=audit.get('day_weighting') or {}
    exhibition=[sum(x.values())/len(x) for x in day.get('exhibition_scores',[]) if isinstance(x,dict)]
    sum_diff=day.get('sum_difference_signed') or []
    exhibition_consistency=agreement(exhibition,sum_diff) if len(exhibition)==len(sum_diff)==6 and any(abs(x)>1e-9 for x in sum_diff) else 0.65
    support=[x.get('total',0.5) for x in (audit.get('practical_support') or {}).get('scores',[])]
    top3=[probs['win'][i]+probs['second'][i]+probs['third'][i] for i in range(6)]
    form_consistency=agreement(support,top3) if len(support)==6 else 0.5
    environment_consistency=0.9 if audit.get('tide_present') and audit.get('weather_present') else (0.7 if audit.get('tide_present') or audit.get('weather_present') else 0.5)
    score=.25*head_score+.22*scenario_agreement+.13*stability+.12*environment_consistency+.12*exhibition_consistency+.11*form_consistency+.05*completeness
    if completeness<0.35: score=min(score,0.61)
    # A or S requires both a sufficiently clear head and focused conditional
    # second/third distributions. Competing head scenarios remain visible but
    # prevent an over-confident grade.
    if head_score<0.45 or stability<0.05 or scenario_conflict>=0.60:
        score=min(score,0.6199)
    if score>=.78:g='S'
    elif score>=.62:g='A'
    elif score>=.45:g='B'
    else:g='見'
    return {'grade':g,'score':round(score,4),'components':{'head_clarity':round(head_score,4),'head_candidate_focus':round(candidate_focus,4),'scenario_conflict':round(scenario_conflict,4),'scenario_agreement':round(scenario_agreement,4),'conditional_stability':round(stability,4),'environment_consistency':round(environment_consistency,4),'exhibition_consistency':round(exhibition_consistency,4),'season_class_local_consistency':round(form_consistency,4),'data_completeness':round(completeness,4),'contradiction_risk':round(contradictions,4)}}
