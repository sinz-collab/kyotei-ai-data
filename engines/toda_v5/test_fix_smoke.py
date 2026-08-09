import copy
from toda_sab_engine_v5 import judge_sab
import toda_live_review_v5 as live_review
from toda_live_review_v5 import _actual_course_map, _refresh_changed_profiles, apply_live_review


# Entry-profile regression: only boats whose actual course changed are reloaded.
class FakeMaster:
    def __init__(self):
        self.calls = []

    def course_profile(self, racer, course):
        self.calls.append((int(racer["lane"]), course))
        return {"course": course, "avg_st": .10 + course / 100}


racers = [{"lane": i} for i in range(1, 7)]
profiles = {str(i): {"course": i, "avg_st": .20} for i in range(1, 7)}
course_map, changed = _actual_course_map({"data": {"actual_entry": [1, 2, 4, 3, 5, 6]}})
master = FakeMaster()
refreshed = _refresh_changed_profiles(racers, profiles, course_map, master)
assert changed is True
assert refreshed == [3, 4]
assert master.calls == [(3, 4), (4, 3)]
assert profiles["3"]["course"] == 4 and profiles["4"]["course"] == 3

# With no entry change, preserve every existing profile and avoid master lookup.
unchanged_profiles = {str(i): {"course": i, "marker": object()} for i in range(1, 7)}
original_profiles = dict(unchanged_profiles)
master = FakeMaster()
refreshed = _refresh_changed_profiles([{"lane": i} for i in range(1, 7)], unchanged_profiles, {i: i for i in range(1, 7)}, master)
assert refreshed == [] and master.calls == []
assert all(unchanged_profiles[str(i)] is original_profiles[str(i)] for i in range(1, 7))

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
 "oneWeak":False,"sab":"A","ai":[],"aiUpset":[],"tickets":[],"probabilityFlow":{},
 "tidePhase":"下げ","tideType":"中潮",
 "modelInputs":{
  "racers":[{"lane":i,"avg_st":.16,"nat_win":5.0} for i in range(1,7)],
  "profiles":{str(i):{"avg_st":.16,"win_rate":10,"top3_vs_course_avg":0} for i in range(1,7)},
  "baseScores":{str(i):7-i for i in range(1,7)}
 }
}
direct={"status":"complete","complete":True,"data":{"wind_speed":4,"wave_height":3,"racers":[{"lane":i,"parts_exchange":[],"weight_adjustment":0} for i in range(1,7)]}}
ex={"status":"complete","complete":True,"data":{"entries":[{"lane":i,"exhibition_time":6.80+i*.01,"exhibition_course":i} for i in range(1,7)]}}
orig={"status":"complete","complete":True,"data":{"entries":[{"lane":i,"lap_time":38+i*.1} for i in range(1,7)]}}
contexts=[]
original_detect_scenarios=live_review.detect_scenarios
def capture_context(racers, profiles, base_scores, context):
    contexts.append(dict(context))
    return original_detect_scenarios(racers, profiles, base_scores, context)
live_review.detect_scenarios=capture_context
pred_same_entry=copy.deepcopy(pred)
direct_same_entry=copy.deepcopy(direct)
direct_same_entry["data"]["actual_entry"]=[1,2,3,4,5,6]
assert apply_live_review(pred,{"direct":direct,"exhibition":ex,"original_exhibition":orig})
assert apply_live_review(pred_same_entry,{"direct":direct_same_entry,"exhibition":ex,"original_exhibition":orig})
live_review.detect_scenarios=original_detect_scenarios
for key in ("win","second","third","secondByHead","thirdByHead","scenarios","sab","ai","aiUpset","tickets"):
    assert pred_same_entry[key]==pred[key], key
assert pred["tickets"], pred
assert pred["liveReviewMeta"]["ticketsRegenerated"] is True
assert pred["liveReviewMeta"]["headConditionalsRegenerated"] is True
assert pred["probabilityFlow"]["reviewed"] is True
assert pred["tidePhase"]=="下げ" and pred["tideType"]=="中潮"
assert contexts and contexts[-1]=={"wind_speed":4,"wave_height":3,"tide_phase":"下げ","tide_type":"中潮"}
print("toda_v5_20260807_fix smoke tests passed")
