from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SUMMARY_PATH = ROOT / "heiwajima_tide_wind_summary_v2.csv"

def _num(v, default=0.0):
    try:
        if v in (None, "", "-"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default

def _wind_speed_band(value):
    speed = _num(value, None)
    if speed is None: return "unknown"
    if speed <= 1: return "0_1m"
    if speed <= 3: return "2_3m"
    if speed <= 5: return "4_5m"
    return "6m_plus"

def _norm_wind(value):
    s = str(value or "").strip()
    mapping = {
        "N":"北","NE":"北東","E":"東","SE":"南東","S":"南","SW":"南西","W":"西","NW":"北西",
        "north":"北","northeast":"北東","east":"東","southeast":"南東",
        "south":"南","southwest":"南西","west":"西","northwest":"北西",
    }
    return mapping.get(s, s or "unknown")

@lru_cache(maxsize=1)
def _rows():
    if not SUMMARY_PATH.exists():
        return []
    with SUMMARY_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))

def _matches(row, context):
    for key in ("tide_type_est","tide_level_band","tide_direction","tide_phase","tide_window","wind_dir","wind_speed_band"):
        required = str(row.get(key) or "ALL")
        if required in ("ALL", "", "nan"):
            continue
        if str(context.get(key) or "unknown") != required:
            return False
    return True

def _specificity(row):
    keys = ("tide_type_est","tide_level_band","tide_direction","tide_phase","tide_window","wind_dir","wind_speed_band")
    return sum(1 for key in keys if str(row.get(key) or "ALL") not in ("ALL", "", "nan"))

def water_features(race):
    tide = race.get("tide") or {}
    weather = race.get("weather") or {}
    context = {
        "tide_type_est": tide.get("tide_type_est") or tide.get("tide_type") or "unknown",
        "tide_level_band": tide.get("tide_level_band") or tide.get("level_band") or "unknown",
        "tide_direction": tide.get("tide_direction") or tide.get("direction") or "unknown",
        "tide_phase": tide.get("tide_phase") or tide.get("phase") or "unknown",
        "tide_window": tide.get("tide_window") or tide.get("nearest") or "unknown",
        "wind_dir": _norm_wind(weather.get("wind_direction") or weather.get("windDirection")),
        "wind_speed_band": _wind_speed_band(weather.get("wind_speed") or weather.get("wind_speed_mps") or weather.get("windSpeed")),
    }
    eligible = [
        row for row in _rows()
        if row.get("sample_reliability") in ("A","B")
        and row.get("condition_scope") != "baseline"
        and _matches(row, context)
    ]
    eligible.sort(key=lambda row: (_specificity(row), int(float(row.get("condition_races") or 0))), reverse=True)

    if not eligible:
        return {
            "center_bias":0.0,"outer_bias":0.0,"escape_bias":0.0,
            "wind_speed":_num(weather.get("wind_speed"),0.0),
            "wave_height":_num(weather.get("wave_height"),0.0),
            "empirical":False,"condition_id":None,"condition_races":0,
        }

    row = eligible[0]
    shrink = 0.70 if row.get("sample_reliability") == "A" else 0.45
    escape = _num(row.get("one_escape_vs_all_pt"),0.0)/100.0*1.35*shrink
    center = _num(row.get("center_head_3_4_vs_all_pt"),0.0)/100.0*1.20*shrink
    outer = _num(row.get("outer_in3_5_6_vs_all_pt"),0.0)/100.0*0.90*shrink
    escape = max(-0.085,min(0.070,escape))
    center = max(-0.075,min(0.085,center))
    outer = max(-0.065,min(0.075,outer))
    return {
        "center_bias":center,"outer_bias":outer,"escape_bias":escape,
        "wind_speed":_num(weather.get("wind_speed"),0.0),
        "wave_height":_num(weather.get("wave_height"),0.0),
        "empirical":True,
        "condition_id":row.get("condition_id"),
        "condition_races":int(float(row.get("condition_races") or 0)),
        "sample_reliability":row.get("sample_reliability"),
    }
