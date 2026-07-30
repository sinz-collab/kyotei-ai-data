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
