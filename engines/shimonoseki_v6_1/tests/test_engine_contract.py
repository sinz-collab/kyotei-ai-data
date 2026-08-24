from __future__ import annotations
import copy, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent))
from shimonoseki_v6_1.shimonoseki_engine_v6_1 import ShimonosekiSiteEngineV61, _probability_review
from shimonoseki_v6_1.tests.test_regression_20260823 import race, docs, TARGET

def run():
    e=ShimonosekiSiteEngineV61(ROOT/'master')
    # Regression: target tickets must arise from inputs only.
    for rno in (4,5,7):
        r=race(rno); pre=e.preliminary_race(r,[]); direct,ex,orig=docs(rno)
        out=e.final_race(r,pre,direct,ex,orig,[])
        tickets=out['ai']+out['balance']+out['aiUpset']
        assert TARGET[rno] in tickets, (rno,TARGET[rno],tickets)
        if rno==7: assert TARGET[rno] in out['ai']
        for k in ('win','second','third'):
            assert abs(sum(out[k].values())-100)<=.05, (rno,k,sum(out[k].values()))
        assert len(tickets)==10 and len(set(tickets))==10
        assert out['sab'] in ('S','A','B','見')
        assert out['debug']['result_used'] is False and out['debug']['odds_used'] is False

        # Leakage test: adding arbitrary result/odds to race input must not change prediction.
        r2=race(rno); r2['result']={'order':['6','6','6'],'payout':'999999'}; r2['odds']={'1-2-3':9999}
        pre2=e.preliminary_race(r2,[])
        out2=e.final_race(r2,pre2,direct,ex,orig,[])
        for k in ('win','second','third','ai','balance','aiUpset','sab'):
            assert out[k]==out2[k], (rno,'leakage',k,out[k],out2[k])

    # Exact delta contract used by site UI.
    pre={'win':{'1':48,'2':10,'3':10,'4':10,'5':11,'6':11},'second':{'1':22,'2':15,'3':16,'4':16,'5':16,'6':15},'third':{'1':14,'2':17,'3':17,'4':17,'5':18,'6':17}}
    fin={'win':{'1':51.3,'2':9,'3':9,'4':9,'5':10.7,'6':11},'second':{'1':19.8,'2':15,'3':17,'4':17,'5':16.2,'6':15},'third':{'1':13.2,'2':17,'3':17,'4':17,'5':18.8,'6':17}}
    rev=_probability_review(pre,fin)
    assert rev['1']['deltaWin']==3.3
    assert rev['1']['deltaSecond']==-2.2
    assert rev['1']['deltaThird']==-0.8
    assert set(rev)==set('123456')
    print('engine_contract: OK')

if __name__=='__main__': run()
