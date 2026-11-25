import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from engine.predictor import predict

def test_output_contract():
 p=json.loads((ROOT/'samples/tokoname_20260730_R01_input.json').read_text(encoding='utf-8'))
 o=predict(p,ROOT/'models')
 assert len(o['probabilities'])==6
 for key in ('win','second','third'): assert abs(sum(x[key] for x in o['probabilities'])-100)<0.5
 ts=o['tickets']['main']+o['tickets']['deviation']+o['tickets']['upset']
 assert len(ts)==10 and len({x['combination'] for x in ts})==10
 assert [len(o['tickets'][k]) for k in ('main','deviation','upset')]==[6,2,2]
 assert o['sab']['independent_of_ticket_count'] is True

def test_st_blend_research_option():
 p=json.loads((ROOT/'samples/tokoname_20260730_R01_input.json').read_text(encoding='utf-8'))
 baseline=predict(p,ROOT/'models')
 assert baseline==predict(p,ROOT/'models',st_blend_weight=0.0)
 blended=predict(p,ROOT/'models',st_blend_weight=0.5)
 for key in ('win','second','third'):
  assert abs(sum(x[key] for x in blended['probabilities'])-100)<0.5
 assert len({t['combination'] for bucket in blended['tickets'].values() for t in bucket})==10
 try:
  predict(p,ROOT/'models',st_blend_weight=1.5)
 except ValueError:
  pass
 else:
  assert False, 'invalid blend weight should be rejected'
