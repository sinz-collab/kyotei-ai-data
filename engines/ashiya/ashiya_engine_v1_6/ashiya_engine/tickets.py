from __future__ import annotations
from itertools import permutations


def ticket_prob(order, p, structure=None):
    a, b, c = (x - 1 for x in order)
    score = p['win'][a] * p['second'][b] / max(1e-9, 1 - p['second'][a]) * p['third'][c] / max(1e-9, 1 - p['third'][a] - p['third'][b])
    # Head-specific scenario linkage. This is the key difference from ranking only
    # by unconditional 1st/2nd/3rd marginals.
    for link in (structure or {}).get('conditional_links', []):
        if order[0] != int(link['head']):
            continue
        if order[1] == int(link['boat']):
            score *= 1 + float(link.get('second_bonus', 0))
        if order[2] == int(link['boat']):
            score *= 1 + float(link.get('third_bonus', 0))
    # Exact head -> second -> third scenario linkage. This prevents the third boat
    # from reverting to unconditional marginal rank after the first two positions
    # have already defined the race development.
    for link in (structure or {}).get('conditional_triplets', []):
        if order[0] == int(link['head']) and order[1] == int(link['second']) and order[2] == int(link['third']):
            score *= 1 + float(link.get('bonus', 0))
    return score


def generate(p, structure):
    scored = []
    for o in permutations(range(1, 7), 3):
        scored.append((ticket_prob(o, p, structure), o))
    scored.sort(reverse=True)
    used = set(); main = []; deviation = []; upset = []

    def add(bucket, o, score, reason):
        if o in used:
            return False
        used.add(o)
        bucket.append({'combination': '-'.join(map(str, o)), 'score': round(score, 8), 'reason': reason})
        return True

    for score, o in scored:
        if len(main) >= 6:
            break
        add(main, o, score, '確率上位・頭別シナリオ連動')

    heads = {int(x['attacker']) for x in structure['scenarios']} | {1}
    for score, o in scored:
        if len(deviation) >= 2:
            break
        if o not in used and (o[0] in heads or o[0] == int(main[0]['combination'].split('-')[0])):
            add(deviation, o, score, '本線の頭または連下ズレ')

    attacker = structure.get('attacker')
    for score, o in scored:
        if len(upset) >= 2:
            break
        if o not in used and o[0] != 1 and (o[0] == attacker or o[0] >= 3):
            add(upset, o, score, '非1頭の展開成立保険')

    for score, o in scored:
        if o in used:
            continue
        if len(deviation) < 2:
            add(deviation, o, score, '次点補充')
        elif len(upset) < 2:
            add(upset, o, score, '次点補充')
        if len(main) + len(deviation) + len(upset) == 10:
            break
    return {'main': main, 'deviation': deviation, 'upset': upset, 'all': main + deviation + upset}
