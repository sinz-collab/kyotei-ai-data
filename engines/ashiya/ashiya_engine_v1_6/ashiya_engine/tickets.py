from __future__ import annotations
from itertools import permutations


def _head_conditional(structure, head):
    rows=(structure or {}).get('head_conditionals',{})
    return rows.get(head) or rows.get(str(head))


def _scenario_candidate(structure, head):
    return next((x for x in (structure or {}).get('head_candidates',[]) if int(x.get('lane',0))==head),{})


def ticket_score_audit(order, p, structure=None):
    a, b, c = (x - 1 for x in order)
    conditional=_head_conditional(structure,order[0])
    if conditional:
        second=conditional['second']
        third=conditional['third']
        third_after_second=third[c]/max(1e-9,1-third[b])
        score=p['win'][a]*second[b]*third_after_second
    else:
        second=p['second']; third=p['third']
        second_given_head=second[b]/max(1e-9,1-second[a])
        third_after_second=third[c]/max(1e-9,1-third[a]-third[b])
        score=p['win'][a]*second_given_head*third_after_second

    audit=(conditional or {}).get('scenario_audit') or {}
    boat_audit={int(x['lane']):x for x in audit.get('boats',[])}
    second_audit=(boat_audit.get(order[1]) or {}).get('second') or {}
    third_audit=(boat_audit.get(order[2]) or {}).get('third') or {}
    candidate=_scenario_candidate(structure,order[0])
    legacy_multiplier=1.0

    # Before the independent conditional layer existed these links had to be
    # applied here.  With scenario_audit present they are already embedded in
    # conditional_second/third, so applying them again would double count them.
    if not audit:
        for link in (structure or {}).get('conditional_links', []):
            if order[0] != int(link['head']):
                continue
            if order[1] == int(link['boat']):
                legacy_multiplier *= 1 + float(link.get('second_bonus', 0))
            if order[2] == int(link['boat']):
                legacy_multiplier *= 1 + float(link.get('third_bonus', 0))
        score*=legacy_multiplier

    # Exact head -> second -> third scenario linkage. This prevents the third boat
    # from reverting after the first two positions define the development.
    triplet_multiplier=1.0
    for link in (structure or {}).get('conditional_triplets', []):
        if order[0] == int(link['head']) and order[1] == int(link['second']) and order[2] == int(link['third']):
            triplet_multiplier *= 1 + float(link.get('bonus', 0))
    score*=triplet_multiplier

    return {
        'head_probability':p['win'][a],
        'conditional_second_probability':second[b] if conditional else second_given_head,
        'conditional_third_probability':third[c],
        'third_after_second_probability':third_after_second,
        'head_scenario_score':float(candidate.get('scenario_score') or 0),
        'head_scenario_strength':float(audit.get('scenario_strength') or 0),
        'second_linked_boat_support':float(second_audit.get('material_support') or 0),
        'third_linked_boat_support':float(third_audit.get('material_support') or 0),
        'second_role_score':sum(float(x.get('score') or 0) for x in second_audit.get('additions',[])),
        'third_role_score':sum(float(x.get('score') or 0) for x in third_audit.get('additions',[])),
        'conditional_second_delta':float(second_audit.get('delta_from_marginal') or 0),
        'conditional_third_delta':float(third_audit.get('delta_from_marginal') or 0),
        'legacy_link_multiplier':legacy_multiplier,
        'triplet_multiplier':triplet_multiplier,
        'final_ticket_score':score,
    }


def ticket_prob(order, p, structure=None):
    return ticket_score_audit(order,p,structure)['final_ticket_score']


def generate(p, structure):
    scored = []
    for o in permutations(range(1, 7), 3):
        audit=ticket_score_audit(o,p,structure)
        scored.append((audit['final_ticket_score'],o,audit))
    scored.sort(key=lambda x:(-x[0],x[1]))
    used = set(); main = []; deviation = []; upset = []

    def add(bucket, o, score, reason, audit, category):
        if o in used:
            return False
        used.add(o)
        components={k:round(v,8) if isinstance(v,float) else v for k,v in audit.items()}
        bucket.append({
            'combination': '-'.join(map(str, o)),
            'score': round(score, 8),
            'category': category,
            'reason': reason,
            'score_components':components,
        })
        return True

    for score, o, audit in scored:
        if len(main) >= 6:
            break
        add(main,o,score,'確率上位・頭別条件付き連動',audit,'main')

    # 6-3-1: deviation tickets first cover a same-head second/third swap when
    # it remains competitive, then follow the conditional probability order.
    score_lookup={o:(score,audit) for score,o,audit in scored}
    for _,o,_ in list(scored):
        if len(deviation)>=3:
            break
        if o not in used:
            continue
        swapped=(o[0],o[2],o[1])
        if swapped in used or swapped not in score_lookup:
            continue
        score,audit=score_lookup[swapped]
        add(
            deviation,swapped,score,
            f'{o[0]}頭の2着・3着入れ替わり（条件付き確率評価）',
            audit,'deviation',
        )

    main_heads={o[0] for _,o,_ in scored if o in used}
    for score,o,audit in scored:
        if len(deviation)>=3:
            break
        if o not in used and o[0] in main_heads:
            add(
                deviation,o,score,
                '本線同一頭の競り合い・追走・差し残りを条件付き確率で補完',
                audit,'deviation',
            )

    # One scenario hole is selected only when a supported head scenario has a
    # concrete linked boat in second or third.  Otherwise the highest remaining
    # probability is used and the fallback is explicitly auditable.
    scenario_by_head={int(x.get('head',0)):x for x in structure.get('head_scenarios',[]) if x.get('head')}
    scenario_choices=[]
    for score,o,audit in scored:
        if o in used:
            continue
        scenario=scenario_by_head.get(o[0])
        if not scenario:
            continue
        linked=set(int(x) for x in scenario.get('linked_boats',[]) if x)
        linked_count=int(o[1] in linked)+int(o[2] in linked)
        if not linked_count:
            continue
        scenario_score=float(scenario.get('score') or 0)
        grounded_score=score*(1.0+0.18*linked_count+0.12*scenario_score)
        scenario_choices.append((grounded_score,score,o,audit,scenario,linked_count))
    scenario_choices.sort(key=lambda x:(-x[0],x[2]))
    if scenario_choices:
        _,score,o,audit,scenario,linked_count=scenario_choices[0]
        roles=', '.join(str(x.get('role')) for x in scenario.get('relations',[]) if int(x.get('boat') or 0) in o[1:])
        add(
            upset,o,score,
            f"シナリオ穴: {scenario.get('scenario_id')}・連動{linked_count}艇"+(f'（{roles}）' if roles else ''),
            audit,'scenario_hole',
        )

    for score, o, audit in scored:
        if o in used:
            continue
        if len(deviation) < 3:
            add(deviation,o,score,'有効なズレ候補不足のため条件付き確率順位で補完',audit,'deviation')
        elif len(upset) < 1:
            add(upset,o,score,'有効なシナリオ穴候補不足のため条件付き確率順位で補完',audit,'scenario_hole')
        if len(main) + len(deviation) + len(upset) == 10:
            break
    ordered=main+deviation+upset
    for index,ticket in enumerate(ordered,1):
        ticket['selection_order']=index
    return {
        'model_version':'ashiya_v51_631',
        'allocation':{'main':6,'deviation':3,'scenario_hole':1},
        'main': main,
        'deviation': deviation,
        'upset': upset,
        'all': ordered,
    }
