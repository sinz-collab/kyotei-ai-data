def _num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default

def water_features(race):
    tide=race.get("tide") or {}; weather=race.get("weather") or {}
    level=str(tide.get("tide_level_band") or tide.get("level_band") or "")
    direction=str(tide.get("tide_direction") or tide.get("direction") or "").lower()
    tide_cm=_num(tide.get("tide_cm"),999)
    wind=_num(weather.get("wind_speed") or weather.get("wind_speed_mps"),0)
    wave=_num(weather.get("wave_height") or weather.get("wave_height_cm"),0)
    center=outer=escape=0.0
    low=("低" in level) or tide_cm < 55
    if low: center += .055; outer += .035; escape -= .025
    if direction in ("falling","下げ潮","ebb"): center += .025
    if wind >= 5: center += .025; escape -= .025
    if wave >= 5: outer += .025; escape -= .020
    # 上限を設け、水面だけで頭を決めない
    center=max(-.08,min(.10,center)); outer=max(-.06,min(.08,outer)); escape=max(-.08,min(.06,escape))
    return {"center_bias":center,"outer_bias":outer,"escape_bias":escape,"low_tide":low,"wind_speed":wind,"wave_height":wave}
