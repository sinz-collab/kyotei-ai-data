
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
 assert len(y['tickets']['deviation'])==3
 assert len(y['tickets']['upset'])==1
 assert len({t['combination'] for t in y['tickets']['all']})==10
 assert y['tickets']['allocation']=={'main':6,'deviation':3,'scenario_hole':1}
 assert [t['selection_order'] for t in y['tickets']['all']]==list(range(1,11))
 assert y['audit']['trifecta_joint']['combinationCount']==120
 assert y['audit']['trifecta_joint']['sumValid'] is True
 assert y['audit']['trifecta_joint']['firstMarginalsValid'] is True
 assert y['audit']['exhibition_v51']['status']=='disabled_unvalidated'
 assert y['audit']['odds_used'] is False
 assert y['audit']['normalized'] is True
