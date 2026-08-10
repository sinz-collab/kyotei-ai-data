
from __future__ import annotations
import json, math, tempfile, os
from pathlib import Path
from typing import Any

def as_int(value: Any, default: int = 0) -> int:
    try: return int(float(value))
    except (TypeError, ValueError): return default

def as_float(value: Any, default: float = 0.0) -> float:
    try:
        x=float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError): return default

def norm_reg_no(value: Any) -> str:
    n=as_int(value,-1)
    if n < 0: return ""
    return f"{n:04d}"

def normalize(values: list[float], floor: float=1e-9) -> list[float]:
    vals=[max(floor, as_float(v,0.0)) for v in values]
    s=sum(vals)
    if s<=0: return [1/len(vals)]*len(vals)
    return [v/s for v in vals]

def atomic_write_json(path: str|Path, payload: dict) -> None:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise
