from __future__ import annotations
import argparse, importlib.util, json, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parent.parent if HERE.parent.name=='engines' else HERE.parent
if str(REPO) not in sys.path: sys.path.insert(0,str(REPO))
try:
    from engines.shimonoseki_v6_1.shimonoseki_engine_v6_1 import ShimonosekiSiteEngineV61, ENGINE_ID, ENGINE_VERSION
except ImportError:
    from shimonoseki_v6_1.shimonoseki_engine_v6_1 import ShimonosekiSiteEngineV61, ENGINE_ID, ENGINE_VERSION

def load_json(p): return json.loads(Path(p).read_text(encoding='utf-8'))
def atomic_write(p,payload):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); t=p.with_suffix(p.suffix+'.tmp'); t.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); t.replace(p)

def build_dynamic_motor_master(date, source_root, master_dir, work_dir):
    raw=Path(source_root)/'下関'/date.replace('-','')/'races'
    if not raw.is_dir() or not list(raw.glob('race_*_motor.txt')): return {}
    module_path=HERE/'build_shimonoseki_motor_recent10_master_v1.py'
    if not module_path.is_file():
        raise RuntimeError('dynamic motor source exists but v6.1 motor builder is missing; copy the unchanged v6 production builder before cutover')
    spec=importlib.util.spec_from_file_location('shimo_motor_builder',module_path); mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    Path(work_dir).mkdir(parents=True,exist_ok=True)
    snapshot=mod.collect_snapshot(raw,date)
    rows=mod.build_master(Path(master_dir)/'shimonoseki_motor_type_master_v1.csv',snapshot,Path(work_dir)/'motor_master.csv')
    return {str(r.get('motor_no') or '').lstrip('0') or '0':r for r in rows}

def race_dir(root,date,race):
    root=Path(root)
    if (root/'direct.json').is_file(): return root
    for p in (root/date/'shimonoseki'/f'{race:02d}', root/'shimonoseki'/f'{race:02d}'):
        if p.is_dir(): return p
    return root/date/'shimonoseki'/f'{race:02d}'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--stage',choices=('preliminary','final'),required=True); ap.add_argument('--race',type=int); ap.add_argument('--data-root',default='data'); ap.add_argument('--master-dir',default=str(HERE/'master')); ap.add_argument('--source-root',default='work/races'); ap.add_argument('--live-root',default='/opt/sinz-edge/data/live'); ap.add_argument('--no-latest',action='store_true'); a=ap.parse_args()
    data=Path(a.data_root); dated=data/'venues'/'shimonoseki'/f"{a.date.replace('-','')}.json"
    payload=load_json(dated)
    if payload.get('venueId')!='shimonoseki' or payload.get('date')!=a.date: raise RuntimeError('Shimonoseki payload identity mismatch')
    # Production precision gate: the existing recent10 fallback must be migrated before old v6 retirement.
    recent10=Path(a.master_dir)/'shimonoseki_motor_recent10_master_v1.csv'
    if not recent10.is_file(): raise RuntimeError('motor recent10 master missing: migrate it from current v6 before cutover')
    eng=ShimonosekiSiteEngineV61(a.master_dir)
    if a.stage=='preliminary':
        dynamic=build_dynamic_motor_master(a.date,a.source_root,a.master_dir,data/'runtime'/'shimonoseki'/a.date.replace('-',''))
        payload=eng.apply_preliminary_daily(payload,dynamic or None)
    else:
        if not a.race or not 1<=a.race<=12: raise ValueError('--race 1..12 required')
        rd=race_dir(a.live_root,a.date,a.race); docs={n:load_json(rd/f'{n}.json') for n in ('direct','exhibition','original_exhibition')}
        for n,d in docs.items():
            if d.get('complete') is not True or d.get('status')!='complete': raise RuntimeError(f'{n}.json incomplete')
        payload=eng.apply_final_race(payload,a.race,docs['direct'],docs['exhibition'],docs['original_exhibition'])
    ok,reason=eng.validate_payload(payload,True)
    if not ok: raise RuntimeError(f'v6.1 payload invalid: {reason}')
    atomic_write(dated,payload)
    if not a.no_latest: atomic_write(data/'venues'/'shimonoseki'/'latest.json',payload)
    print(json.dumps({'venue':'shimonoseki','date':a.date,'stage':a.stage,'race':a.race,'engine':ENGINE_ID,'engineVersion':ENGINE_VERSION,'status':'complete'},ensure_ascii=False))
    return 0
if __name__=='__main__': raise SystemExit(main())
