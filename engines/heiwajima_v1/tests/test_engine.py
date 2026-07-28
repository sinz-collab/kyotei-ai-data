import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from heiwajima_prediction_engine import calculate
from heiwajima_live_review import apply_live_update

def sample(): return json.loads((ROOT/"examples"/"sample_input_pre.json").read_text(encoding="utf-8"))
def test_probabilities_normalize():
    o=calculate(sample())
    for k in ("win_prob","second_prob","third_prob"): assert abs(sum(x[k] for x in o["probabilities"])-1)<1e-4
    assert o["odds_used_for_prediction"] is False
def test_entry_change_reanalysis():
    f=apply_live_update(sample(),{"entries":[{"boat_no":3,"actual_course":4},{"boat_no":4,"actual_course":3}]})
    o=calculate(f); assert o["data_completeness"]["entry_changed"] is True
    assert next(x for x in o["probabilities"] if x["boat_no"]==3)["actual_course"]==4
def test_engine_version_and_scenario_link():
    o=calculate(sample()); assert o["engine_version"].startswith("heiwajima_v1_codex_rebuild")
    assert all("head" in s and "second" in s and "third" in s for s in o["scenarios"])
def test_no_extreme_default_inside_bias():
    o=calculate(sample()); p=next(x["win_prob"] for x in o["probabilities"] if x["boat_no"]==1)
    assert p < .60
