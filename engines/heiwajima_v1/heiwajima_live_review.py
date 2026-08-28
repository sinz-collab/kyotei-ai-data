from copy import deepcopy

def apply_live_update(pre_input, live):
    data=deepcopy(pre_input); data['stage']='final'
    engine_live=deepcopy(live)
    # Odds are display-only and must never enter the prediction engine context.
    engine_live.pop('odds',None)
    data['live']=engine_live
    actual={int(x['boat_no']):int(x['actual_course']) for x in live.get('entries',[])}
    exhibitions={int(x['boat_no']):x for x in live.get('exhibitions',[])}
    for b in data['boats']:
        no=int(b['boat_no'])
        if no in actual: b['actual_course']=actual[no]
        if no in exhibitions: b['exhibition']=exhibitions[no]
    for k in ('weather','tide','stabilizer','shortened_laps'):
        if k in live: data[k]=live[k]
    return data
