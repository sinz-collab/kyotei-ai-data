
from __future__ import annotations
import argparse,json
from pathlib import Path
from .engine import AshiyaEngine
from .utils import atomic_write_json

def main():
 p=argparse.ArgumentParser(); p.add_argument('--input',required=True); p.add_argument('--output',required=True); p.add_argument('--models',required=True); p.add_argument('--player-db',required=True); p.add_argument('--stage',default='pre',choices=['pre','live','final']); a=p.parse_args()
 payload=json.loads(Path(a.input).read_text(encoding='utf-8'))
 engine=AshiyaEngine(a.models,a.player_db)
 out=engine.predict(payload,stage=a.stage); atomic_write_json(a.output,out)
 print(json.dumps({'output':a.output,'sab':out['sab']['grade'],'tickets':len(out['tickets']['all']),'coverage':out['audit']['model']['coverage']},ensure_ascii=False))
if __name__=='__main__': main()
