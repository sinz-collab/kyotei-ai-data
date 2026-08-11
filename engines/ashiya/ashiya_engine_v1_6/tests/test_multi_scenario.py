from ashiya_engine.sab import grade
from ashiya_engine.scenario import build_head_conditionals, detect
from ashiya_engine.tickets import generate, ticket_score_audit


def make_racers():
    rows=[]
    for lane in range(1,7):
        rows.append({
            'lane':lane,'actual_course':lane,'class':'A2','nat_win':5.5,'local_win':5.6,
            'motor_2':35+lane,'season_runs':[{'finish':'2','entry_course':lane}],
            'exhibition_time':6.80+lane/100,'lap_time':37+lane/100,
            'turn_time':8+lane/100,'straight_time':7.7+lane/100,
            'sum':44+lane/100,'sum_difference':0.1,
            'start_timing':0.10+lane/100,'boaters_makuri_rate':10,
        })
    rows[2].update({'start_timing':0.30,'lap_time':36.8,'turn_time':7.8,'sum':43.6,'sum_difference':0.4})
    return rows


def probs():
    return {'win':[.30,.15,.12,.11,.17,.15],'second':[1/6]*6,'third':[1/6]*6}


def test_dent_supported_boat_remains_a_head_candidate():
    structure=detect(make_racers(),probs(),[{} for _ in range(6)])
    candidates={x['lane']:x for x in structure['head_candidates']}
    assert 3 in candidates
    assert candidates[3]['dent'] is True
    scenario=next(x for x in structure['head_scenarios'] if x['head']==3)
    assert scenario['linked_boats']==[4,1,5,6]


def test_actual_course_drives_links_and_each_distribution_normalizes():
    rows=make_racers()
    rows[4]['actual_course']=6
    rows[5]['actual_course']=5
    structure=detect(rows,probs(),[{} for _ in range(6)])
    conditionals=build_head_conditionals(probs(),structure)
    # Lane 6 is actual course 5, so lane 5 is its direct outside link.
    scenario=next(x for x in structure['head_scenarios'] if x['head']==6)
    assert scenario['actual_course']==5
    assert scenario['linked_boats'][0]==5
    for row in conditionals.values():
        assert abs(sum(row['second'])-1)<1e-10
        assert abs(sum(row['third'])-1)<1e-10


def test_sab_does_not_reach_a_when_heads_compete_and_conditionals_disperse():
    p=probs()
    structure=detect(make_racers(),p,[{} for _ in range(6)])
    structure['head_conditionals']=build_head_conditionals(p,structure)
    audit={
        'model':{'coverage':.6},'entry_changed':False,'actual_entry_complete':True,
        'tide_present':True,'weather_present':True,
        'day_weighting':{'exhibition_scores':[{'win':.5,'second':.5,'third':.5} for _ in range(6)],'sum_difference_signed':[0]*6},
        'practical_support':{'scores':[{'total':.5} for _ in range(6)]},
    }
    result=grade(p,structure,audit)
    assert result['components']['scenario_conflict']>0
    assert result['grade'] not in ('S','A')


def test_head_conditionals_expose_independent_scenario_score_breakdown():
    p=probs()
    structure=detect(make_racers(),p,[{} for _ in range(6)])
    conditionals=build_head_conditionals(p,structure)
    head4=conditionals[4]
    audit=head4['scenario_audit']
    assert audit['actual_course']==4
    boat5=next(x for x in audit['boats'] if x['lane']==5)
    boat3=next(x for x in audit['boats'] if x['lane']==3)
    assert any(x['role']=='5直外連動' for x in boat5['second']['additions'])
    assert boat3['second']['penalty']>0
    assert abs(sum(head4['second'])-1)<1e-10
    assert abs(sum(head4['third'])-1)<1e-10


def test_course_six_head_scenario_is_weaker_than_course_five():
    p=probs()
    structure=detect(make_racers(),p,[{} for _ in range(6)])
    by_lane={x['lane']:x for x in structure['head_candidates']}
    by_lane[5]['scenario_score']=0.2
    by_lane[6]['scenario_score']=0.2
    conditionals=build_head_conditionals(p,structure)
    assert conditionals[6]['scenario_audit']['scenario_strength'] < conditionals[5]['scenario_audit']['scenario_strength']


def test_ticket_generation_uses_conditionals_and_keeps_bucket_sizes():
    p=probs()
    structure=detect(make_racers(),p,[{} for _ in range(6)])
    structure['head_conditionals']=build_head_conditionals(p,structure)
    audit=ticket_score_audit((4,5,6),p,structure)
    conditional=structure['head_conditionals'][4]
    assert audit['conditional_second_probability']==conditional['second'][4]
    assert audit['conditional_third_probability']==conditional['third'][5]
    assert audit['legacy_link_multiplier']==1.0
    tickets=generate(p,structure)
    assert [len(tickets[x]) for x in ('main','deviation','upset')]==[6,2,2]
    assert len({x['combination'] for x in tickets['all']})==10
    assert all('score_components' in x for x in tickets['all'])
