from pathlib import Path
import json
from ashiya_engine.engine import AshiyaEngine
ROOT=Path(__file__).resolve().parents[1]
def test_entry_change_recalculates():
    e=AshiyaEngine(ROOT/'data/models',ROOT/'data/player_db')
    x=json.loads((ROOT/'samples/sample_input_pre.json').read_text(encoding='utf-8'))
    a=e.predict(x)
    x['racers'][2]['actual_course']=4
    x['racers'][3]['actual_course']=3
    b=e.predict(x,stage='live')
    assert b['audit']['entry_changed'] is True
    assert a['probabilities'] != b['probabilities']
    assert len(b['tickets']['all']) == 10
