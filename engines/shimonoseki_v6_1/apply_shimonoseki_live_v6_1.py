from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path
HERE=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--race',required=True,type=int); ap.add_argument('--data-root',default='data'); ap.add_argument('--live-root',required=True); ap.add_argument('--master-dir'); a=ap.parse_args()
    cmd=[sys.executable,str(HERE/'run_shimonoseki_v6_1.py'),'--date',a.date,'--stage','final','--race',str(a.race),'--data-root',a.data_root,'--live-root',a.live_root]
    if a.master_dir: cmd += ['--master-dir',a.master_dir]
    return subprocess.call(cmd)
if __name__=='__main__': raise SystemExit(main())
