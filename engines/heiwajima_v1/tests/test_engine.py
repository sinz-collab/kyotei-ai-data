import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from heiwajima_prediction_engine import calculate
from heiwajima_live_review import apply_live_update
from heiwajima_ticket_engine import generate_tickets

def sample(): return json.loads((ROOT/"examples"/"sample_input_pre.json").read_text(encoding="utf-8"))
def test_probabilities_normalize():
    data=sample(); data["max_tickets"]=10
    o=calculate(data)
    for k in ("win_prob","second_prob","third_prob"): assert abs(sum(x[k] for x in o["probabilities"])-1)<1e-4
    assert o["odds_used_for_prediction"] is False
    assert len(o["tickets"]) == 10
def test_entry_change_reanalysis():
    f=apply_live_update(sample(),{"entries":[{"boat_no":3,"actual_course":4},{"boat_no":4,"actual_course":3}]})
    o=calculate(f); assert o["data_completeness"]["entry_changed"] is True
    assert next(x for x in o["probabilities"] if x["boat_no"]==3)["actual_course"]==4
def test_engine_version_and_scenario_link():
    o=calculate(sample()); assert o["engine_version"] == "heiwajima_complete_v2_6_20260829"
    assert all("head" in s and "second" in s and "third" in s for s in o["scenarios"])
def test_no_extreme_default_inside_bias():
    o=calculate(sample()); p=next(x["win_prob"] for x in o["probabilities"] if x["boat_no"]==1)
    assert p < .60

def test_live_composite_context_reaches_engine_without_odds():
    live={
        "entries":[{"boat_no":i,"actual_course":i} for i in range(1,7)],
        "exhibitions":[{"boat_no":i,"lap_score":0.1*i} for i in range(1,7)],
        "slit":{"1":"dent","4":"advance"},
        "exhibition_st":{"1":0.30,"4":0.04},
        "straight_rank":{"1":6,"4":1},
        "odds":{"1-2-3":"9.9"},
    }
    final=apply_live_update(sample(),live)
    assert final["live"]["slit"]["4"] == "advance"
    assert final["live"]["exhibition_st"]["1"] == 0.30
    assert final["live"]["straight_rank"]["4"] == 1
    assert "odds" not in final["live"]

def test_ticket_reserves_all_comparable_heads_and_conditional_outer():
    win={1:.438,2:.157,3:.179,4:.081,5:.090,6:.055}
    boats=[
        {"boat_no":lane,"win_prob":win[lane],"second_prob":1/6,"third_prob":1/6}
        for lane in range(1,7)
    ]
    tickets=generate_tickets(boats,[],max_tickets=10)
    heads={ticket["first"] for ticket in tickets}
    assert len(tickets) == 10
    assert {2,3,5}.issubset(heads)

def test_ticket_does_not_split_near_equal_non_axis_heads():
    win={1:.440,2:.111,3:.170,4:.167,5:.085,6:.027}
    boats=[
        {"boat_no":lane,"win_prob":win[lane],"second_prob":1/6,"third_prob":1/6}
        for lane in range(1,7)
    ]
    tickets=generate_tickets(boats,[],max_tickets=10)
    heads={ticket["first"] for ticket in tickets}
    assert len(tickets) == 10
    assert {3,4}.issubset(heads)
    assert 5 not in heads and 6 not in heads
