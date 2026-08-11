
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone, timedelta
from .models import ModelEnsemble
from .db import PlayerDB
from .probability import blend,apply_day_adjustment,apply_practical_support
from .scenario import detect,apply,build_head_conditionals
from .tickets import generate
from .sab import grade
from .utils import norm_reg_no,as_int

JST=timezone(timedelta(hours=9))
class AshiyaEngine:
    def __init__(self,model_dir,player_db_dir,config=None):
        self.models=ModelEnsemble(model_dir); self.db=PlayerDB(player_db_dir); self.config=config or {}
    def predict(self,race,stage='pre'):
        racers=race.get('racers') or race.get('entries') or []
        if len(racers)!=6: raise ValueError('six racers required')
        for i,r in enumerate(racers,1):
            r.setdefault('lane',i); r['reg_no']=self.db.resolve_reg_no(r.get('reg_no') or r.get('player_id'),r.get('name') or r.get('player_name'))
            r['actual_course']=r.get('actual_course') or r.get('entry_course') or r['lane']
        actual_entry=race.get('actual_entry')
        if isinstance(actual_entry,list) and len(actual_entry)==6:
            try:
                lanes=[int(x) for x in actual_entry]
            except (TypeError,ValueError):
                lanes=[]
            if sorted(lanes)==list(range(1,7)):
                course_by_lane={lane:course for course,lane in enumerate(lanes,1)}
                for r in racers: r['actual_course']=course_by_lane[as_int(r['lane'])]
        model_probs,model_audit=self.models.predict(race)
        base,db_audit=blend(model_probs,racers,self.db,model_audit['coverage'])
        day_base,day_audit=apply_day_adjustment(base,racers,race)
        supported,support_audit=apply_practical_support(day_base,racers,race)
        structure=detect(racers,supported,db_audit.get('player_lookups')); final=apply(supported,structure)
        structure['head_conditionals']=build_head_conditionals(final,structure)
        entry_changed=any(as_int(r['actual_course'])!=as_int(r['lane']) for r in racers)
        audit={'model':model_audit,'database':db_audit,'day_weighting':day_audit,'practical_support':support_audit,'entry_changed':entry_changed,'actual_entry_complete':all(r.get('actual_course') for r in racers),'actual_course_by_lane':{as_int(r['lane']):as_int(r['actual_course']) for r in racers},'tide_present':bool(race.get('tide')),'weather_present':bool(race.get('weather')),'odds_used':False,'full_reflection':True}
        sab=grade(final,structure,audit); tickets=generate(final,structure)
        audit.update({'ticket_count':len(tickets['all']),'duplicate_count':len(tickets['all'])-len({x['combination'] for x in tickets['all']}),'normalized':all(abs(sum(final[k])-1)<1e-8 for k in final),'probability_sums':{k:round(sum(final[k]),10) for k in final}})
        probs=[]
        for i,r in enumerate(racers): probs.append({'lane':r['lane'],'reg_no':r['reg_no'],'name':r.get('name') or r.get('player_name'),'win':round(final['win'][i],6),'second':round(final['second'][i],6),'third':round(final['third'][i],6),'top3':round(min(1,final['win'][i]+final['second'][i]+final['third'][i]),6)})
        return {'engine':{'name':'ashiya_prediction_engine','version':'1.6.1','master_version':'v4.2','stage':stage,'generated_at':datetime.now(JST).isoformat()},'race':{'date':race.get('date') or race.get('race_date'),'race_no':race.get('race_no') or race.get('race')},'probabilities':probs,'attack_structure':{k:v for k,v in structure.items() if k!='scenarios'},'scenarios':structure['scenarios'],'sab':sab,'tickets':tickets,'audit':audit}
