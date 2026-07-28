import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from heiwajima_prediction_engine import calculate
from heiwajima_live_review import apply_live_update

def test_probabilities_normalize():
 d=json.loads((ROOT/'examples'/'sample_input_pre.json').read_text(encoding='utf-8')); o=calculate(d)
 for k in ('win_prob','second_prob','third_prob'): assert abs(sum(x[k] for x in o['probabilities'])-1)<1e-4
 assert o['odds_used_for_prediction'] is False

def test_entry_change_reanalysis():
 d=json.loads((ROOT/'examples'/'sample_input_pre.json').read_text(encoding='utf-8'))
 f=apply_live_update(d,{'entries':[{'boat_no':3,'actual_course':4},{'boat_no':4,'actual_course':3}]})
 o=calculate(f); assert o['data_completeness']['entry_changed'] is True
 assert next(x for x in o['probabilities'] if x['boat_no']==3)['actual_course']==4
