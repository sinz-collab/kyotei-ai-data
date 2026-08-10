
from pathlib import Path
import json
from ashiya_engine.engine import AshiyaEngine
ROOT=Path(__file__).resolve().parents[1]
def test_full_run():
 e=AshiyaEngine(ROOT/'data/models',ROOT/'data/player_db')
 x=json.loads((ROOT/'samples/sample_input_pre.json').read_text(encoding='utf-8'))
 y=e.predict(x)
 assert len(y['probabilities'])==6
 assert len(y['tickets']['main'])==6
 assert len(y['tickets']['deviation'])==2
 assert len(y['tickets']['upset'])==2
 assert len({t['combination'] for t in y['tickets']['all']})==10
 assert y['audit']['odds_used'] is False
 assert y['audit']['normalized'] is True
