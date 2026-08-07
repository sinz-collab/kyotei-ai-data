import math, re
LANES = (1,2,3,4,5,6)
def num(v, default=0.0):
    if v is None or v == "": return default
    try:
        s = re.sub(r"[^\d.\-]", "", str(v))
        return float(s) if s not in ("","-",".") else default
    except Exception:
        return default
def clamp(v, lo, hi): return max(lo, min(hi, v))
def normalize_map(values, floor=0.05):
    vals = {str(k): max(floor, float(values.get(str(k), values.get(k, floor)))) for k in LANES}
    total = sum(vals.values()) or 1.0
    out = {k: round(v/total*100, 1) for k,v in vals.items()}
    diff = round(100.0-sum(out.values()), 1)
    best = max(out, key=out.get); out[best] = round(out[best]+diff,1); return out
def norm_name(s): return re.sub(r"[\s　]+", "", str(s or ""))
def exp_softmax(scores, scale=0.45): return normalize_map({k: math.exp(v*scale) for k,v in scores.items()})
