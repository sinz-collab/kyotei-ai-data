"""Research reproduction ONLY; LIVE/exhibition input required. No outcome data used."""
import json, math
from pathlib import Path
import numpy as np

CONFIG = json.loads((Path(__file__).parent/'wakamatsu_v3_frozen_coefficients.json').read_text(encoding='utf-8'))

def number(row,key):
    try:
        v=float(row.get(key,''))
        return v if math.isfinite(v) else float('nan')
    except (ValueError,TypeError): return float('nan')

def first(row,*keys):
    for key in keys:
        v=number(row,key)
        if math.isfinite(v): return v
    return float('nan')

def safe_mean(v):
    v=[x for x in v if math.isfinite(x)]
    return float(np.mean(v)) if v else float('nan')

def relative(v):
    if not math.isfinite(v[0]):return float('nan')
    a=[x for x in v if math.isfinite(x)]
    if len(a)<5:return float('nan')
    return (sum(x<v[0] for x in a)+.5*sum(x==v[0] for x in a)-.5)/(len(a)-1)

def v2_feature_values(r):
    course=first(r,'boat1_player_course_win_rate_pct'); starts=first(r,'boat1_player_course_starts'); esc=first(r,'boat1_escape_rate_pct')
    lap=[first(r,f'boat{i}_original_lap_time') for i in range(1,7)]
    turn=[first(r,f'boat{i}_original_turn_time') for i in range(1,7)]
    straight=[first(r,f'boat{i}_original_straight_time') for i in range(1,7)]
    win=[first(r,f'boat{i}_live_win_pct') for i in range(1,7)]
    st=[first(r,f'boat{i}_avg_st') for i in range(1,7)]
    strongest=max([a for a in win[1:] if math.isfinite(a)],default=float('nan'))
    wind=first(r,'pred_wind_speed_mps','wind_speed_mps')
    low=first(r,'pred_minutes_to_low_tide','minutes_to_low_tide')
    return {
      'lane1_course_strength': ((course-55)/30)*min(1,max(0,starts)/20)**.5 if math.isfinite(course) and math.isfinite(starts) else float('nan'),
      'lane1_escape_history':(esc-50)/30 if math.isfinite(esc) else float('nan'),
      'lane1_local_rating':(first(r,'boat1_local_win_rate')-5.5)/2,
      'lane1_st_advantage': ((st[0]-safe_mean(st[1:]))/.04) if math.isfinite(st[0]) else float('nan'),
      'lane1_lap_rank':relative(lap),'lane1_turn_rank':relative(turn),'lane1_straight_rank':relative(straight),
      'attacker_live_strength':(strongest-20)/15,
      'best_attack_st': ((st[0]-min([x for x in st[1:] if math.isfinite(x)],default=float('nan')))/.04) if math.isfinite(st[0]) else float('nan'),
      'wall_st_gap':((st[1]-st[2])/.04) if math.isfinite(st[1]) and math.isfinite(st[2]) else float('nan'),
      'wind_speed':(wind-2.5)/2,'race_no':(first(r,'race_no')-6.5)/4,
      'tide_rising':1. if low<0 else 0.,'tide_near_low':1. if abs(low)<=60 else 0.,
    }

def score_v2(row):
    f=v2_feature_values(row); c=CONFIG['v2']; xs=[]
    for k in c['features']:
        x=f[k]
        if not math.isfinite(x):x=c['impute_medians'][k]
        x=np.clip(x,-3,3)
        x=np.clip((x-c['center'][k])/c['scale'][k],-3,3)
        xs.append(x)
    z=float(np.dot(xs,c['coefs']))
    return float(1/(1+np.exp(-np.clip(z,-20,20))))

def v3_feature_values(row, tide):
    lap=np.array([first(row,f'boat{i}_original_lap_time') for i in range(1,7)],float)
    turn=np.array([first(row,f'boat{i}_original_turn_time') for i in range(1,7)],float)
    def adv(v):return float(np.nanmedian(v[1:])-v[0]) if np.isfinite(v[0]) and np.isfinite(v[1:]).any() else float('nan')
    high=float(tide['minutes_from_high_tide']); low=float(tide['minutes_from_low_tide'])
    h=float(np.exp(-np.clip(high,0,600)/90));l=float(np.exp(-np.clip(low,0,600)/90))
    rising=1.0 if str(tide['tide_phase']).lower()=='rising' else 0.0
    la=adv(lap);ta=adv(turn);pressure=h-l
    return {'lap_adv':la,'turn_adv':ta,'lap_x_tide':la*pressure,'turn_x_tide':ta*pressure,'lap_x_rising':la*(rising-.5)}

def predict_live(row,tide,*,strict=True):
    """Input only as known pre-deadline. No morning fallback or historical outcome lookup."""
    if strict:
        for key in ['boat1_live_win_pct','boat1_original_lap_time','boat1_original_turn_time']:
            if not math.isfinite(number(row,key)):raise ValueError('missing required LIVE feature '+key)
        for key in ['tide_phase','minutes_from_high_tide','minutes_from_low_tide']:
            if key not in tide or tide[key] is None: raise ValueError('missing validated tide '+key)
        if tide['tide_phase'] not in ('rising','falling'):raise ValueError('invalid tide phase')
    existing=1-first(row,'boat1_live_win_pct')/100
    if not 0<=existing<=1:raise ValueError('invalid LIVE probability')
    v2=score_v2(row)
    base=np.clip(.1*existing+.9*v2,.001,.999)
    c=CONFIG['v3']; f=v3_feature_values(row,tide);xs=[]
    for k in c['features']:
        x=f[k]
        if not math.isfinite(x):x=c['median'][k]
        xs.append(np.clip((x-c['center'][k])/c['scale'][k],-4,4))
    logit=np.log(base/(1-base)); z=logit+float(np.dot(xs,c['coefs']))
    final=1/(1+np.exp(-np.clip(z,-30,30)))
    return {'existing_fly_pct':existing*100,'v2_fly_pct':v2*100,'v3_no_tide_pct':base*100,'v3_lane1_tide_pct':final*100,'source':'live','model':CONFIG['schema_version']}
