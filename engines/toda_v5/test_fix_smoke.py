import copy
from toda_sab_engine_v5 import judge_sab
from toda_live_review_v5 import apply_live_review

# SAB string-key regression test: this case must be eligible for S.
win={"1":55.0,"2":25.0,"3":8.0,"4":5.0,"5":4.0,"6":3.0}
cond={str(h):{str(l):(0.1 if l==h else (10.0 if l==6 else 20.0)) for l in range(1,7)} for h in range(1,7)}
# normalize-ish sufficient for linked count
sab,axis,gap=judge_sab(win,[{"head":1,"weight":.9,"links":[2,3,4,5,6]}],cond,cond)
assert sab=="S" and axis==1, (sab,axis,gap)

# Live review regression: tickets/conditionals must change and metadata must confirm regeneration.
pred={
 "win":{"1":40,"2":30,"3":10,"4":8,"5":7,"6":5},
 "second":{"1":.1,"2":30,"3":25,"4":20,"5":15,"6":9.9},
 "third":{"1":.1,"2":25,"3":20,"4":20,"5":18,"6":16.9},
 "secondByHead":copy.deepcopy(cond),"thirdByHead":copy.deepcopy(cond),
 "scenarios":[{"head":1,"weight":.9,"links":[2,3,4,5,6]},{"head":2,"weight":.8,"links":[1,3,4,5,6]}],
 "oneWeak":False,"sab":"A","ai":[],"aiUpset":[],"tickets":[],"probabilityFlow":{}
}
direct={"status":"complete","complete":True,"data":{"wind_speed":4,"wave_height":3,"racers":[{"lane":i,"parts_exchange":[],"weight_adjustment":0} for i in range(1,7)]}}
ex={"status":"complete","complete":True,"data":{"entries":[{"lane":i,"exhibition_time":6.80+i*.01,"exhibition_course":i} for i in range(1,7)]}}
orig={"status":"complete","complete":True,"data":{"entries":[{"lane":i,"lap_time":38+i*.1} for i in range(1,7)]}}
assert apply_live_review(pred,{"direct":direct,"exhibition":ex,"original_exhibition":orig})
assert pred["tickets"], pred
assert pred["liveReviewMeta"]["ticketsRegenerated"] is True
assert pred["liveReviewMeta"]["headConditionalsRegenerated"] is True
assert pred["probabilityFlow"]["reviewed"] is True
print("toda_v5_20260807_fix smoke tests passed")
