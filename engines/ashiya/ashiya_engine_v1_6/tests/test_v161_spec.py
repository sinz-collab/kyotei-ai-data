from ashiya_engine.probability import apply_day_adjustment, apply_practical_support, day_profile
from ashiya_engine.scenario import build_head_conditionals
from scripts.predict_venue_json import apply_actual_entry


def racers():
    rows=[]
    for lane in range(1,7):
        rows.append({
            'lane':lane,'actual_course':lane,'motor_2':33,'motor_3':50,
            'class':'B1','nat_win':5,'local_win':5,'local_2':35,'local_3':55,
            'season_runs':[],'exhibition_rank':lane,'lap_time':37+lane/100,
            'turn_time':8+lane/100,'straight_time':7.7+lane/100,
            'sum':44+lane/100,'sum_difference':0,
        })
    return rows


def uniform():
    return {key:[1/6]*6 for key in ('win','second','third')}


def test_day_profiles_match_formal_spec_and_day_four_is_not_final():
    assert day_profile({'eventDay':1})['exhibition'] == .22
    assert day_profile({'eventDay':2})['season'] == .08
    assert day_profile({'eventDay':3})['season'] == .12
    assert day_profile({'eventDay':4})['name'] == 'middle_day'
    assert day_profile({'eventDay':6,'eventDayLabel':'最終日'})['name'] == 'final_day'
    assert day_profile({'eventDay':6,'eventDayLabel':'6日目'})['name'] == 'middle_day'


def test_sum_difference_is_bounded_multiplicative_and_all_places_normalize():
    rows=racers()
    rows[0]['sum_difference']=10
    rows[1]['sum_difference']=-10
    out,audit=apply_day_adjustment(uniform(),rows,{'eventDay':2,'eventDayLabel':'2日目'})
    for key in ('win','second','third'):
        assert abs(sum(out[key])-1)<1e-12
        assert out[key][0]>out[key][1]
    assert audit['sum_difference_signed'][:2] == [1.0,-1.0]


def test_practical_support_weights_are_day_specific():
    expected={
        1:(.18,.28),2:(.22,.24),3:(.26,.20),4:(.28,.17),
    }
    for day,(season,motor) in expected.items():
        _,audit=apply_practical_support(uniform(),racers(),{'eventDay':day})
        assert audit['weights']['season']==season
        assert audit['weights']['motor']==motor
    _,audit=apply_practical_support(uniform(),racers(),{'eventDay':6,'eventDayLabel':'最終日'})
    assert audit['weights']=={'season':.32,'local':.24,'class':.20,'motor':.14,'season_course':.10}


def test_actual_entry_overrides_stale_courses_without_using_source_flag():
    rows=racers()
    for row in rows:
        row['actual_course']=7-row['lane']
    assert apply_actual_entry(rows,[1,2,4,3,5,6]) is True
    assert {r['lane']:r['actual_course'] for r in rows} == {1:1,2:2,3:4,4:3,5:5,6:6}
    assert apply_actual_entry(rows,None) is False
    assert {r['lane']:r['actual_course'] for r in rows} == {i:i for i in range(1,7)}


def test_every_head_gets_normalized_actual_course_conditionals():
    structure={'course_by_lane':{1:1,2:2,3:4,4:3,5:5,6:6},'conditional_links':[]}
    conditional=build_head_conditionals(uniform(),structure)
    assert set(conditional)==set(range(1,7))
    for head,values in conditional.items():
        assert values['second'][head-1]<1e-8
        assert values['third'][head-1]<1e-8
        assert abs(sum(values['second'])-1)<1e-12
        assert abs(sum(values['third'])-1)<1e-12
    # Boat 4 is actual course 3, so its head scenario lifts boat 3 at course 4.
    assert conditional[4]['second'][2] > conditional[4]['second'][1]
