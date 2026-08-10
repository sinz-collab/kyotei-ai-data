
from __future__ import annotations
from pathlib import Path
import joblib, numpy as np
from .features import FeatureBuilder
from .utils import normalize

class ModelEnsemble:
    def __init__(self, model_dir):
        p=Path(model_dir)
        self.paths={'win':p/'ashiya_lgbm_win.pkl','second':p/'ashiya_lgbm_2nd.pkl','third':p/'ashiya_lgbm_3rd.pkl'}
        self.objects={k:joblib.load(v) for k,v in self.paths.items()}
        self.builder=FeatureBuilder(self.paths['win'])
        base=self.objects['win']['feature_cols']
        for k,o in self.objects.items():
            if list(o['feature_cols'])!=list(base): raise ValueError(f'feature mismatch: {k}')
    def predict(self,race):
        X,audit=self.builder.build(race); out={}
        for target,obj in self.objects.items():
            ps=[]
            for model in obj['models']:
                ps.append(model.predict_proba(X)[:,1])
            out[target]=normalize(np.mean(ps,axis=0).tolist())
        return out,audit
