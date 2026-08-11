from __future__ import annotations
from .utils import as_float, as_int, normalize


def kimarite(racer):
    k = racer.get('kimarite') or racer.get('boaters_kimarite') or {}
    direct = {
        'escape': racer.get('boaters_escape_rate', 0),
        'sashi': racer.get('boaters_sashi_rate', 0),
        'makuri': racer.get('boaters_makuri_rate', 0),
        'makurizashi': racer.get('boaters_makuri_sashi_rate', 0),
        'nuki': racer.get('boaters_nuki_rate', 0),
        'megumare': racer.get('boaters_megumare_rate', 0),
    }

    def rate(name):
        v = as_float(k.get(name + '_rate', k.get(name, direct.get(name, 0))))
        return v / 100 if v > 1 else v

    return {n: rate(n) for n in ['escape', 'sashi', 'makuri', 'makurizashi', 'nuki', 'megumare']}


def _rank_score(value, reverse=False):
    """Convert an already supplied 1-6 rank to 0-1 quality."""
    rank = as_int(value, 0)
    if not 1 <= rank <= 6:
        return 0.5
    score = (7 - rank) / 6
    return 1 - score if reverse else score


def _finish_value(text):
    s = str(text or '')
    for i in range(1, 7):
        if s.startswith(str(i)):
            return i
    return None


def _season_score(racer):
    runs = racer.get('season_runs') or []
    vals = [_finish_value(x.get('finish')) for x in runs]
    vals = [x for x in vals if x]
    if not vals:
        return 0.5
    avg = sum(vals) / len(vals)
    top3 = sum(v <= 3 for v in vals) / len(vals)
    return max(0.0, min(1.0, 0.55 * ((6.5 - avg) / 5.5) + 0.45 * top3))


def _outside_vigor(racer):
    """Attack/linkage vigor for 5/6 when 4 is the attacker.

    Exhibition ST is only one weak component. Lap/SUM, exhibition rank,
    season form, motor and kimarite must agree before a strong bonus is given.
    """
    k = kimarite(racer)
    exhibit = _rank_score(racer.get('exhibition_rank'))
    lap = _rank_score(racer.get('lap_rank') or racer.get('original_lap_rank'))
    total = _rank_score(racer.get('sum_rank') or racer.get('original_sum_rank'))
    st = as_float(racer.get('start_timing') or racer.get('exhibition_st'), 0.18)
    st_score = max(0.0, min(1.0, (0.24 - st) / 0.18))
    motor = max(0.0, min(1.0, as_float(racer.get('motor_2') or racer.get('motor_2_rate'), 33.0) / 55.0))
    season = _season_score(racer)
    attack = min(1.0, (k['makuri'] + k['makurizashi']) * 2.5)
    return max(0.0, min(1.0,
        0.22 * exhibit + 0.20 * lap + 0.18 * total + 0.10 * st_score +
        0.12 * motor + 0.13 * season + 0.05 * attack
    ))



def _local_st_profile(racer, db_lookup=None):
    row=(db_lookup or {}).get('st_course') or {}
    starts=as_int(row.get('starts'),0)
    local=as_float(row.get('avg_st'),0)
    rel=str(row.get('reliability') or '')
    if local<=0:
        local=as_float(racer.get('local_st') or racer.get('avg_st'),0.18)
    reliability=min(1.0, starts/12.0)
    if rel in ('A','B'): reliability=max(reliability,0.75)
    elif rel=='C': reliability=max(reliability,0.45)
    elif rel=='参考': reliability=min(reliability,0.35)
    return local,reliability,starts,rel

def _slit_adjustment(info, db_lookups=None):
    # Exhibition ST is contextual. Compare it with local course ST and adjacent boats.
    db_lookups=db_lookups or []
    for i,x in enumerate(info):
        local,rel,starts,label=_local_st_profile(x['racer'], db_lookups[i] if i<len(db_lookups) else None)
        x['local_st']=local; x['local_st_reliability']=rel; x['local_st_starts']=starts; x['local_st_label']=label
        # positive means better than own Ashiya-course baseline
        x['st_delta']=max(-0.12,min(0.12,local-x['st']))
    for x in info:
        inside=[y for y in info if y['course']==x['course']-1]
        outside=[y for y in info if y['course']==x['course']+1]
        x['inside_adv']=(inside[0]['st']-x['st']) if inside else 0.0
        x['outside_adv']=(x['st']-outside[0]['st']) if outside else 0.0
    return info


def _median(values):
    ordered=sorted(values)
    middle=len(ordered)//2
    return (ordered[middle-1]+ordered[middle])/2 if len(ordered)%2==0 else ordered[middle]


def _field_top_half(info, item, keys):
    """Whether a lower-is-better exhibition value is in the field's top half."""
    def value(row):
        for key in keys:
            v=as_float(row['racer'].get(key),0.0)
            if v>0:
                return v
        return 0.0
    current=value(item)
    field=[value(row) for row in info]
    field=[v for v in field if v>0]
    return bool(current and len(field)>=2 and current<=_median(field))


def _extract_head_candidates(info, probs, dent):
    """Select supported head candidates without allowing exhibition ST to veto one."""
    max_win=max(probs['win'])
    season_median=_median([_season_score(x['racer']) for x in info])
    motor_values=[as_float(x['racer'].get('motor_2') or x['racer'].get('motor_2_rate'),33.0) for x in info]
    motor_median=_median(motor_values)
    candidates=[]
    for item in info:
        win=probs['win'][item['lane']-1]
        if win < 0.10 and win < max_win*0.40:
            continue
        racer=item['racer']
        evidence=[]
        grade=str(racer.get('class') or racer.get('grade') or '').upper()
        if grade in ('A1','A2'):
            evidence.append('class')
        nat=as_float(racer.get('nat_win') or racer.get('national_win_rate'),0.0)
        local=as_float(racer.get('local_win') or racer.get('local_win_rate'),0.0)
        if local>0 and (nat<=0 or local>=nat):
            evidence.append('local')
        if _season_score(racer)>=season_median:
            evidence.append('meeting_form')
        if as_float(racer.get('motor_2') or racer.get('motor_2_rate'),33.0)>=motor_median:
            evidence.append('motor')
        display_parts=sum([
            _field_top_half(info,item,('exhibition_time',)),
            _field_top_half(info,item,('lap_time','sum_lap')),
            _field_top_half(info,item,('turn_time',)),
            _field_top_half(info,item,('straight_time',)),
            _field_top_half(info,item,('sum',)),
            as_float(racer.get('sum_difference'),0.0)>0,
        ])
        if display_parts>=2:
            evidence.append('exhibition_original')
        if item['course']<=3:
            evidence.append('actual_course')
        if item['attack']>0 or item['sashi']>0:
            evidence.append('attack_style')

        # Probability eligibility alone is insufficient; require agreement from
        # at least two independent existing materials. ST is intentionally not a
        # veto and cannot be the sole supporting material.
        if len(evidence)<2:
            continue
        severity=0.0
        if dent and item['lane']==dent['lane']:
            severity=max(0.0,min(1.0,(-item['st_delta']+max(0,item['inside_adv']))/0.16))
        candidates.append({
            'lane':item['lane'],'actual_course':item['course'],'win':round(win,8),
            'evidence':evidence,'dent':bool(dent and item['lane']==dent['lane']),
            'dent_severity':round(severity,4),
            # Reuse the existing weak dent multiplier; dent never deletes a head.
            'scenario_score':round(win*(1-0.22*severity),8),
        })
    if not candidates:
        best=max(info,key=lambda x:x['base'])
        candidates=[{'lane':best['lane'],'actual_course':best['course'],'win':round(best['base'],8),'evidence':['win_leader','actual_course'],'dent':bool(dent and best['lane']==dent['lane']),'dent_severity':0.0,'scenario_score':round(best['base'],8)}]
    return sorted(candidates,key=lambda x:(-x['scenario_score'],-x['win'],x['lane']))


def _conditional_material_support(info):
    """Existing non-result materials used to support head-conditioned links.

    This audit is separate from head-candidate extraction and from the marginal
    probability corrections.  It only controls how strongly a boat can benefit
    from a race-development link after the head has been fixed.  Exhibition ST
    deliberately has only a five-percent share.
    """
    season_values=[_season_score(x['racer']) for x in info]
    motor_values=[as_float(x['racer'].get('motor_2') or x['racer'].get('motor_2_rate'),33.0) for x in info]
    rows={}
    for item in info:
        racer=item['racer']
        grade=str(racer.get('class') or racer.get('grade') or '').upper()
        class_score={'A1':1.0,'A2':0.82,'B1':0.56,'B2':0.36}.get(grade,0.50)
        nat=as_float(racer.get('nat_win') or racer.get('national_win_rate'),0.0)
        local=as_float(racer.get('local_win') or racer.get('local_win_rate'),0.0)
        local_level=max(0.0,min(1.0,(local-3.0)/5.0)) if local>0 else 0.5
        local_edge=max(0.0,min(1.0,0.5+(local-nat)/4.0)) if local>0 and nat>0 else 0.5
        local_score=0.65*local_level+0.35*local_edge
        season=_season_score(racer)
        motor=as_float(racer.get('motor_2') or racer.get('motor_2_rate'),33.0)
        lo=min(motor_values); hi=max(motor_values)
        motor_score=(motor-lo)/(hi-lo) if hi>lo else 0.5
        display_parts=[
            _rank_score(racer.get('exhibition_rank')),
            _rank_score(racer.get('lap_rank') or racer.get('original_lap_rank')),
            _rank_score(racer.get('turn_rank') or racer.get('original_turn_rank')),
            _rank_score(racer.get('straight_rank') or racer.get('original_straight_rank')),
            _rank_score(racer.get('sum_rank') or racer.get('original_sum_rank')),
        ]
        sum_diff=max(-1.0,min(1.0,as_float(racer.get('sum_difference'),0.0)/0.5))
        exhibition=max(0.0,min(1.0,0.85*(sum(display_parts)/len(display_parts))+0.15*((sum_diff+1.0)/2.0)))
        styles=kimarite(racer)
        attack_style=max(0.0,min(1.0,2.5*(styles['sashi']+styles['makuri']+styles['makurizashi'])))
        st_score=max(0.0,min(1.0,0.5+item.get('st_delta',0.0)/0.16))
        common=(0.24*class_score+0.17*local_score+0.18*season+
                0.16*motor_score+0.20*exhibition+0.05*st_score)
        rows[item['lane']]={
            'second':round(max(0.0,min(1.0,0.88*common+0.12*attack_style)),6),
            'third':round(max(0.0,min(1.0,0.94*common+0.06*attack_style)),6),
            'components':{
                'class':round(class_score,4),'local':round(local_score,4),
                'meeting':round(season,4),'motor':round(motor_score,4),
                'exhibition_original':round(exhibition,4),'attack_style':round(attack_style,4),
                'exhibition_st_weak':round(st_score,4),
            },
        }
    return rows


def _head_scenario(candidate, lane_by_course, candidate_lanes):
    head=candidate['lane']; course=candidate['actual_course']
    relations=[]
    def link(target,role):
        if target and target!=head and target not in [x['boat'] for x in relations]:
            relations.append({'boat':target,'role':role})
    if course==1:
        link(lane_by_course.get(2),'2差し残り')
        link(lane_by_course.get(3),'2逃がしからの3浮上')
        for outer_course in (4,5,6):
            outer=lane_by_course.get(outer_course)
            if outer in candidate_lanes:
                link(outer,'外攻め連動')
    elif course==2:
        link(lane_by_course.get(1),'1内残り')
        link(lane_by_course.get(3),'3連動')
    elif course==3:
        link(lane_by_course.get(4),'4直外連動')
        link(lane_by_course.get(1),'1内残り')
        link(lane_by_course.get(5),'5展開拾い')
        link(lane_by_course.get(6),'6展開拾い')
    elif course==4:
        link(lane_by_course.get(5),'5外連動')
        link(lane_by_course.get(6),'6外連動')
        link(lane_by_course.get(1),'1内残り')
        link(lane_by_course.get(2),'2内残り')
    elif course==5:
        link(lane_by_course.get(6),'6外連動')
        link(lane_by_course.get(1),'1内残り')
    else:
        link(lane_by_course.get(1),'1内残り')
    return {
        'scenario_id':f'H{head}C{course}','head':head,'attacker':head,
        'actual_course':course,'score':candidate['scenario_score'],
        'dent':candidate['dent'],'dent_severity':candidate['dent_severity'],
        'linked_boats':[x['boat'] for x in relations],'relations':relations,
        'evidence':candidate['evidence'],
    }

def detect(racers, probs, db_lookups=None):
    info = []
    by_lane = {}
    for r, p in zip(racers, probs['win']):
        lane = as_int(r.get('lane'))
        course = as_int(r.get('actual_course') or r.get('entry_course'), lane)
        st = as_float(r.get('start_timing') or r.get('exhibition_st') or r.get('avg_st'), 0.18)
        k = kimarite(r)
        attack = k['makuri'] + k['makurizashi']
        item = {'lane': lane, 'course': course, 'st': st, 'attack': attack, 'sashi': k['sashi'], 'base': p, 'racer': r}
        info.append(item)
        by_lane[lane] = item

    info=_slit_adjustment(info,db_lookups)
    by_lane={x['lane']:x for x in info}

    wall = max((x for x in info if x['course'] in (2, 3)), key=lambda x: (-x['st'], x['base']), default=None)
    dent = max(info, key=lambda x: (x['st'] - 0.14, -x['base']))
    course_by_lane={x['lane']:x['course'] for x in info}
    lane_by_course={course:lane for lane,course in course_by_lane.items()}
    head_candidates=_extract_head_candidates(info,probs,dent)
    candidate_lanes={x['lane'] for x in head_candidates}
    head_scenarios=[_head_scenario(x,lane_by_course,candidate_lanes) for x in head_candidates]
    candidates = [x for x in info if x['course'] in (2, 3, 4, 5)]
    attacker = max(candidates, key=lambda x: (0.30*x['attack']+0.30*x['base']+0.20*max(0,x['inside_adv'])+0.12*max(0,x['st_delta'])*x['local_st_reliability']/0.12+0.08*max(0,0.20-x['st'])), default=info[0])

    # Slit-shape override: when course 3 is clearly dented and course 4 is
    # substantially ahead with supporting lap/SUM or motor evidence, course 4
    # becomes the attacker. This is not an ST-only override.
    c3 = next((x for x in info if x['course'] == 3), None)
    c4 = next((x for x in info if x['course'] == 4), None)
    if c3 and c4:
        slit_adv = c3['st'] - c4['st']
        r4 = c4['racer']
        support = (
            0.30 * _rank_score(r4.get('lap_rank') or r4.get('original_lap_rank')) +
            0.30 * _rank_score(r4.get('sum_rank') or r4.get('original_sum_rank')) +
            0.20 * _rank_score(r4.get('exhibition_rank')) +
            0.20 * max(0.0, min(1.0, as_float(r4.get('motor_2') or r4.get('motor_2_rate'), 33.0) / 55.0))
        )
        local_support=0.5+0.5*max(-1,min(1,c4['st_delta']/0.08))*c4['local_st_reliability']
        dent_confirm=(c3['st']-c3['local_st']>=0.06) or (slit_adv>=0.10)
        if slit_adv >= 0.08 and support >= 0.58 and dent_confirm and local_support>=0.48:
            attacker = c4

    scenarios = []
    conditional_links = []
    conditional_triplets = []
    lane = attacker['lane']
    course = attacker['course']
    if course == 2 and attacker['sashi'] >= attacker['attack']:
        scenarios.append({'scenario_id': 'S03', 'name': '2差し頭・1内残り', 'score': 0.65, 'attacker': lane, 'linked_boats': [1, 3]})
    elif course == 3:
        scenarios += [
            {'scenario_id': 'S04', 'name': '3攻め・4直外連動', 'score': 0.62, 'attacker': lane, 'linked_boats': [4, 1]},
            {'scenario_id': 'S05', 'name': '3攻め・1内残り', 'score': 0.45, 'attacker': lane, 'linked_boats': [1, 4]},
        ]
    elif course == 4:
        v5 = _outside_vigor(by_lane[5]['racer']) if 5 in by_lane else 0.0
        v6 = _outside_vigor(by_lane[6]['racer']) if 6 in by_lane else 0.0
        outside_strength = max(v5, v6)
        scenarios += [
            {'scenario_id': 'S06', 'name': '4カド攻め・5/6外連動', 'score': 0.64 + 0.16 * outside_strength, 'attacker': lane, 'linked_boats': [5, 6, 1]},
            {'scenario_id': 'S07', 'name': '4攻め・1内残り', 'score': 0.46, 'attacker': lane, 'linked_boats': [1, 2]},
        ]
        # Head-specific linkage. Bonuses are later used both in marginals and ticket scoring.
        if v5 >= 0.48:
            conditional_links.append({'head': lane, 'boat': 5, 'second_bonus': 0.12 + 0.18 * v5, 'third_bonus': 0.16 + 0.22 * v5, 'vigor': round(v5, 4), 'reason': '4攻め時の5直外連動'})
        if v6 >= 0.48:
            conditional_links.append({'head': lane, 'boat': 6, 'second_bonus': 0.18 + 0.26 * v6, 'third_bonus': 0.12 + 0.20 * v6, 'vigor': round(v6, 4), 'reason': '4攻め時の6外差し・展開拾い'})
        # If 3 is the dent boat, 2 can remain inside after 4 attacks.
        if dent and dent['lane'] == 3:
            conditional_links.append({'head': lane, 'boat': 2, 'second_bonus': 0.02, 'third_bonus': 0.22, 'vigor': 0.55, 'reason': '3凹み時の2内差し残り'})
    elif course == 5:
        scenarios.append({'scenario_id': 'S09', 'name': '5攻め・6外連動', 'score': 0.54, 'attacker': lane, 'linked_boats': [6, 1]})

    # Conditional ticket paths: when the inside boat still has a viable escape,
    # an outside attack can lift its direct outside boat to second while boat 2
    # remains third through an inside差し. This is based on race development,
    # not on the result or odds.
    head1_viable = probs['win'][0] >= max(0.22, sorted(probs['win'], reverse=True)[1] * 0.82)
    boat2 = by_lane.get(2)
    boat2_viable = boat2 is not None and dent['lane'] != 2 and probs['third'][1] >= 0.08
    if head1_viable and boat2_viable and course in (4, 5):
        second_candidates=[]
        if course == 4:
            if 5 in by_lane and _outside_vigor(by_lane[5]['racer']) >= 0.45:
                second_candidates.append(5)
            if 6 in by_lane and _outside_vigor(by_lane[6]['racer']) >= 0.55:
                second_candidates.append(6)
        elif course == 5:
            second_candidates.append(5)
            if 6 in by_lane and _outside_vigor(by_lane[6]['racer']) >= 0.52:
                second_candidates.append(6)
        for sec in second_candidates:
            conditional_triplets.append({
                'head':1,'second':sec,'third':2,'bonus':0.34,
                'reason':'1逃げ＋外攻め連動＋2内差し3着残り'
            })

    scenarios.append({'scenario_id': 'S10', 'name': '外攻め不発・内決着', 'score': 0.40 + probs['win'][0] * 0.25, 'attacker': lane, 'linked_boats': [1, 2, 3]})
    scenarios = sorted(scenarios, key=lambda x: x['score'], reverse=True)[:3]
    ws = normalize([x['score'] for x in scenarios])
    for x, w in zip(scenarios, ws):
        x['probability'] = round(w, 4)
    return {
        'attacker': attacker['lane'],
        'primary_attacker': attacker['lane'],
        'head_candidates': head_candidates,
        'head_scenarios': head_scenarios,
        'conditional_material_support': _conditional_material_support(info),
        'wall_boat': wall['lane'] if wall else None,
        'dent_boat': dent['lane'],
        'scenarios': scenarios,
        'conditional_links': conditional_links,
        'conditional_triplets': conditional_triplets,
        'course_by_lane': course_by_lane,
        'slit_audit':[{'lane':x['lane'],'course':x['course'],'exhibition_st':round(x['st'],3),'local_course_st':round(x['local_st'],3),'local_st_starts':x['local_st_starts'],'local_st_reliability':round(x['local_st_reliability'],3),'st_delta':round(x['st_delta'],3),'inside_adv':round(x['inside_adv'],3)} for x in info],
        'local_venue_audit': {
            x['lane']: _local_venue_profile(
                x['racer'],
                {'local_st_starts':x['local_st_starts'],'local_st_reliability':x['local_st_reliability']}
            ) for x in info
        },
    }


def _local_venue_profile(racer, slit_row=None):
    """Bounded Ashiya venue-performance support for an already identified attacker.

    This is never used alone. Reliability is conservative because the morning JSON
    does not always expose venue sample counts; local-course ST starts are used as
    a partial reliability proxy.
    """
    nat_win=as_float(racer.get('nat_win') or racer.get('national_win_rate'),0.0)
    local_win=as_float(racer.get('local_win') or racer.get('local_win_rate'),0.0)
    local_2=as_float(racer.get('local_2') or racer.get('local_2ren_rate'),0.0)
    local_3=as_float(racer.get('local_3') or racer.get('local_3ren_rate'),0.0)
    # Normalize each component to 0-1. Venue advantage is capped to avoid sparse-sample inflation.
    win_level=max(0.0,min(1.0,(local_win-3.0)/6.0))
    two_level=max(0.0,min(1.0,local_2/70.0))
    three_level=max(0.0,min(1.0,local_3/85.0))
    venue_edge=max(-1.0,min(1.0,(local_win-nat_win)/2.0)) if nat_win>0 and local_win>0 else 0.0
    starts=as_int((slit_row or {}).get('local_st_starts'),0)
    st_rel=as_float((slit_row or {}).get('local_st_reliability'),0.0)
    reliability=max(0.35,min(0.85,0.35+0.35*st_rel+0.15*min(1.0,starts/12.0)))
    raw=0.34*win_level+0.25*two_level+0.19*three_level+0.22*((venue_edge+1.0)/2.0)
    score=max(0.0,min(1.0,raw*reliability))
    return {
        'score':score,'reliability':reliability,'national_win':nat_win,
        'local_win':local_win,'local_2':local_2,'local_3':local_3,
        'venue_edge':venue_edge,'st_proxy_starts':starts
    }


def apply(probs, structure):
    out = {k: list(v) for k, v in probs.items()}
    for s in structure['scenarios']:
        w = s['probability']
        sid = s['scenario_id']
        a = as_int(s['attacker']) - 1
        if sid in ('S03','S04','S09'):
            out['win'][a] *= (1 + 0.10*w)
        elif sid=='S06':
            # 4-course attack: reward the attacker itself when slit and local-ST evidence agree.
            slit=next((z for z in structure.get('slit_audit',[]) if z['lane']==a+1),{})
            strength=max(0.0,min(1.0,(slit.get('inside_adv',0)+0.02)/0.16))*max(0.35,slit.get('local_st_reliability',0.35))
            venue=structure.get('local_venue_audit',{}).get(a+1,{})
            venue_score=max(0.0,min(1.0,as_float(venue.get('score'),0.0)))
            # Venue performance can only amplify a confirmed attack; it cannot create one.
            # The extra multiplier is capped and requires the S06 attack scenario to be active.
            attack_mult=(0.42+0.58*strength) + min(0.38,0.52*venue_score)
            out['win'][a] *= (1 + attack_mult*w)
        for lane in s['linked_boats']:
            i = lane - 1
            out['second'][i] *= (1 + 0.10 * w)
            out['third'][i] *= (1 + 0.12 * w)

    dent=as_int(structure.get('dent_boat'),0)
    if dent:
        d=dent-1
        slit=next((z for z in structure.get('slit_audit',[]) if z['lane']==dent),{})
        severity=max(0.0,min(1.0,(-slit.get('st_delta',0)+max(0,slit.get('inside_adv',0)))/0.16))
        out['win'][d]*=1-0.22*severity
        out['second'][d]*=1-0.14*severity

    # Conditional links are deliberately bounded. They do not alter the head rate;
    # they only reorder 2nd/3rd candidates when that attack scenario is active.
    for link in structure.get('conditional_links', []):
        i = as_int(link['boat']) - 1
        out['second'][i] *= 1 + min(0.45, as_float(link.get('second_bonus')))
        out['third'][i] *= 1 + min(0.45, as_float(link.get('third_bonus')))

    for k in out:
        out[k] = normalize(out[k])
    return out


def build_head_conditionals(probs, structure):
    """Build independent 2nd/3rd distributions after fixing each head.

    Marginals remain the foundation, preserving the existing day, SUM,
    practical-support and exhibition evaluations.  A separate multiplicative
    layer then applies ``head scenario score x linked-boat material support``.
    Every contribution is retained in ``scenario_audit`` for regression review.
    """
    course_by_lane={as_int(k):as_int(v) for k,v in (structure.get('course_by_lane') or {}).items()}
    lane_by_course={course:lane for lane,course in course_by_lane.items()}
    candidates={as_int(x.get('lane')):x for x in structure.get('head_candidates',[])}
    candidate_lanes=set(candidates)
    materials={as_int(k):v for k,v in (structure.get('conditional_material_support') or {}).items()}
    max_scenario=max([as_float(x.get('scenario_score')) for x in candidates.values()] or [1.0])
    result={}

    for head in range(1,7):
        course=course_by_lane.get(head,head)
        candidate=candidates.get(head) or {}
        relative=(as_float(candidate.get('scenario_score'))/max_scenario) if candidate else 0.0
        scenario_strength=(0.55+0.45*relative) if candidate else 0.42
        if course==6:
            scenario_strength*=0.65

        benefits={lane:{'second':[],'third':[]} for lane in range(1,7)}
        penalties={lane:{'second':0.0,'third':0.0,'reasons':[]} for lane in range(1,7)}

        def link(target,role,second_weight,third_weight):
            if not target or target==head:
                return
            support=materials.get(target,{'second':0.5,'third':0.5})
            for place,weight in (('second',second_weight),('third',third_weight)):
                if weight<=0:
                    continue
                material=as_float(support.get(place),0.5)
                score=scenario_strength*material*weight
                benefits[target][place].append({
                    'role':role,'scenario_strength':round(scenario_strength,6),
                    'linked_boat_support':round(material,6),'role_weight':round(weight,6),
                    'score':round(score,6),
                })

        def disadvantage(target,role,second_weight,third_weight):
            if not target or target==head:
                return
            for place,weight in (('second',second_weight),('third',third_weight)):
                score=scenario_strength*weight
                penalties[target][place]+=score
            penalties[target]['reasons'].append(role)

        # All relations use actual course, never frame number.
        if course==1:
            link(lane_by_course.get(2),'2差し残り',0.34,0.20)
            link(lane_by_course.get(3),'2逃がし後の3浮上・3内残り',0.22,0.32)
            for outer_course in (4,5,6):
                outer=lane_by_course.get(outer_course)
                if outer in candidate_lanes:
                    link(outer,f'{outer_course}コース攻め残り',0.12,0.18)
                    link(lane_by_course.get(outer_course+1),f'{outer_course}コース攻めの外連動',0.16,0.20)
        elif course==2:
            link(lane_by_course.get(1),'1内残り',0.26,0.25)
            link(lane_by_course.get(3),'3直外連動',0.23,0.22)
        elif course==3:
            link(lane_by_course.get(4),'4直外連動',0.38,0.28)
            link(lane_by_course.get(5),'5展開拾い',0.16,0.31)
            link(lane_by_course.get(6),'6展開拾い',0.12,0.28)
            link(lane_by_course.get(1),'1内残り',0.22,0.30)
            disadvantage(lane_by_course.get(2),'3攻めで2が展開不利',0.18,0.12)
        elif course==4:
            link(lane_by_course.get(5),'5直外連動',0.36,0.30)
            link(lane_by_course.get(6),'6外連動',0.27,0.34)
            link(lane_by_course.get(1),'1内残り',0.20,0.28)
            link(lane_by_course.get(2),'2内差し残り',0.18,0.25)
            disadvantage(lane_by_course.get(3),'4攻めで3が展開不利',0.20,0.14)
        elif course==5:
            link(lane_by_course.get(6),'6外連動',0.36,0.32)
            link(lane_by_course.get(1),'1内残り',0.22,0.30)
            disadvantage(lane_by_course.get(4),'5攻めで4が展開不利',0.18,0.12)
        elif course==6:
            link(lane_by_course.get(1),'6コース頭時の1残り',0.24,0.26)
            link(lane_by_course.get(2),'内艇の差し残り',0.16,0.24)
            link(lane_by_course.get(3),'内艇の差し残り',0.12,0.20)
            disadvantage(lane_by_course.get(5),'6攻めで5が展開不利',0.16,0.10)

        # Preserve the existing bounded attack-specific evidence as an extra
        # linked role, but still pass it through material support and the head score.
        for old_link in structure.get('conditional_links',[]):
            if as_int(old_link.get('head'))==head:
                link(
                    as_int(old_link.get('boat')),
                    str(old_link.get('reason') or 'existing conditional link'),
                    min(0.45,as_float(old_link.get('second_bonus'))),
                    min(0.45,as_float(old_link.get('third_bonus'))),
                )

        second_raw=[]; third_raw=[]; boats_audit=[]
        for lane in range(1,7):
            if lane==head:
                second_raw.append(0.0); third_raw.append(0.0)
                continue
            support=materials.get(lane,{'second':0.5,'third':0.5,'components':{}})
            row={'lane':lane,'material_components':support.get('components',{})}
            for place,raw_list in (('second',second_raw),('third',third_raw)):
                marginal=as_float(probs[place][lane-1])
                material=as_float(support.get(place),0.5)
                preservation=0.90+0.20*material
                additions=sum(x['score'] for x in benefits[lane][place])
                penalty=penalties[lane][place]
                multiplier=max(0.55,preservation*(1.0+additions-penalty))
                raw=marginal*multiplier
                raw_list.append(raw)
                row[place]={
                    'marginal':round(marginal,8),'material_support':round(material,6),
                    'preservation_multiplier':round(preservation,6),
                    'additions':benefits[lane][place],
                    'penalty':round(penalty,6),'penalty_reasons':penalties[lane]['reasons'],
                    'scenario_multiplier':round(multiplier,6),'raw':round(raw,8),
                }
            boats_audit.append(row)

        second=normalize(second_raw); third=normalize(third_raw)
        for row in boats_audit:
            lane=row['lane']
            row['second']['conditional']=round(second[lane-1],8)
            row['second']['delta_from_marginal']=round(second[lane-1]-as_float(probs['second'][lane-1]),8)
            row['third']['conditional']=round(third[lane-1],8)
            row['third']['delta_from_marginal']=round(third[lane-1]-as_float(probs['third'][lane-1]),8)
        result[head]={
            'second':second,'third':third,
            'scenario_audit':{
                'head':head,'actual_course':course,'candidate':bool(candidate),
                'head_scenario_score':round(as_float(candidate.get('scenario_score')),8),
                'relative_head_scenario_score':round(relative,6),
                'scenario_strength':round(scenario_strength,6),'boats':boats_audit,
            },
        }
    return result
