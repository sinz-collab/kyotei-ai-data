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

    def add(bucket, o, score, reason, audit):
        if o in used:
            return False
        used.add(o)
        components={k:round(v,8) if isinstance(v,float) else v for k,v in audit.items()}
        bucket.append({'combination': '-'.join(map(str, o)), 'score': round(score, 8), 'reason': reason,'score_components':components})
        return True

    for score, o, audit in scored:
        if len(main) >= 6:
            break
        add(main,o,score,'確率上位・頭別条件付き連動',audit)

    candidates=structure.get('head_candidates') or []
    if candidates:
        head_priority={
            int(x['lane']):p['win'][int(x['lane'])-1]*float(x.get('scenario_score') or x.get('win') or 0)
            for x in candidates
        }
    else:
        fallback={int(x['attacker']) for x in structure.get('scenarios',[])} | {max(range(1,7),key=lambda h:p['win'][h-1])}
        head_priority={head:p['win'][head-1] for head in fallback}
    ordered_heads=sorted(head_priority,key=lambda h:(-head_priority[h],h))
    split=max(2,(len(ordered_heads)+1)//2)
    middle_heads=ordered_heads[1:split] or ordered_heads[:1]
    lower_heads=ordered_heads[split:] or ordered_heads[1:] or ordered_heads[:1]

    def add_by_head(bucket,heads,limit,reason):
        # First pass gives distinct supported heads one opportunity. The fill
        # pass then follows score order without equalising every candidate.
        for head in heads:
            choice=next((x for x in scored if x[1] not in used and x[1][0]==head),None)
            if choice:
                add(bucket,choice[1],choice[0],reason,choice[2])
            if len(bucket)>=limit:
                return
        for score,o,audit in scored:
            if len(bucket)>=limit:
                return
            if o not in used and o[0] in heads:
                add(bucket,o,score,reason,audit)

    add_by_head(deviation,middle_heads,2,'中位head candidateの条件付き展開')
    add_by_head(upset,lower_heads,2,'低位だが支持のあるhead scenario')

    for score, o, audit in scored:
        if o in used:
            continue
        if len(deviation) < 2:
            add(deviation,o,score,'次点補充',audit)
        elif len(upset) < 2:
            add(upset,o,score,'次点補充',audit)
        if len(main) + len(deviation) + len(upset) == 10:
            break
    return {'main': main, 'deviation': deviation, 'upset': upset, 'all': main + deviation + upset}
