#!/usr/bin/env python3
"""JSON fixturesを時系列で実行し、Brier/LogLoss/SAB別成績を集計する入口。結果付きfixtureが必要。"""
from pathlib import Path
import json, math, sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from heiwajima_prediction_engine import calculate

def main(folder):
 rows=[]
 for p in sorted(Path(folder).glob('*.json')):
  d=json.loads(p.read_text(encoding='utf-8'))
  if 'result' not in d: continue
  o=calculate(d); winner=int(d['result'][0]); pw=next(x['win_prob'] for x in o['probabilities'] if x['boat_no']==winner)
  rows.append({'file':p.name,'winner_prob':pw,'logloss':-math.log(max(pw,1e-12)),'sab':o['sab']['grade']})
 print(json.dumps(rows,ensure_ascii=False,indent=2))
if __name__=='__main__': main(sys.argv[1])
