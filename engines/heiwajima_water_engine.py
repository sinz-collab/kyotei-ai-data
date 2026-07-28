def water_features(race):
    tide=race.get('tide') or {}; weather=race.get('weather') or {}
    level=str(tide.get('tide_level_band',''))
    direction=str(tide.get('tide_direction',''))
    wind=float(weather.get('wind_speed') or 0); wave=float(weather.get('wave_height') or 0)
    center=0.0; outer=0.0; escape=0.0
    if '低' in level: center+=.08; outer+=.06
    if direction.lower() in ('falling','下げ潮'): center+=.04
    if wind>=5: center+=.05; escape-=.04
    if wave>=5: outer+=.04; escape-=.03
    return {'center_bias':center,'outer_bias':outer,'escape_bias':escape,'wind_speed':wind,'wave_height':wave}
