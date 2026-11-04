from pathlib import Path
import sys, unittest
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/"automation"))
from kiryu_v1_engine import KiryuV1,A,B

class KiryuReplay(unittest.TestCase):
    def test_nov03_probabilities_normal_and_fly(self):
        f=ROOT/"tests/fixtures/kiryu_v1"; boats=pd.read_csv(f/"kiryu_20261103_exhibition_72boats.csv"); expected=pd.read_csv(f/"kiryu_20261103_reproduced_probabilities.csv"); esc=pd.read_csv(f/"kiryu_20261103_boaters_escape_vs_results.csv").set_index("race")
        fly=pd.read_csv(f/"kiryu_fly_independent_races.csv"); fly=fly[fly.period=="Nov03"].set_index("race")
        starts=[[.20,.23,.18,.17,.16,.19],[.23,.17,.17,.18,.17,.21],[.17,.20,.17,.16,.18,.16],[.16,.17,.21,.14,.19,.14],[.13,.17,.16,.16,.17,.17],[.12,.19,.17,.20,.19,.22],[.16,.21,.14,.20,.18,.17],[.17,.18,.19,.17,.15,.17],[.19,.15,.18,.23,.14,.17],[.19,.19,.16,.14,.18,.16],[.14,.17,.16,.18,.20,.17],[.14,.15,.17,.17,.12,.16]]
        eng=KiryuV1(); internal=np.load(f/"expected_internal.npz")
        np.testing.assert_allclose(eng.hist2_pair[A,B],internal["hist2"],rtol=0,atol=1e-12)
        np.testing.assert_allclose(eng.hist3,internal["hist3"],rtol=0,atol=1e-12)
        for rn,g in boats.groupby("race"):
            racers=[]
            for j,row in g.sort_values("lane").reset_index(drop=True).iterrows():
                racers.append({"lane":int(row.lane),"entry_course":int(row.exhibition_course),"player_id":str(int(row.pre_reg_no)),"motor_no":str(int(row.pre_motor_no)),"boat_no":str(int(row.pre_boat_no)),"nat_win":row.national_win_rate,"local_win":row.local_win_rate,"nat_2":row.national_2ren_rate,"local_2":row.local_2ren_rate,"nat_3":0,"local_3":0,"motor_2":row.motor_2ren_rate,"motor_3":row.motor_3ren_rate,"avg_st":starts[int(rn)-1][j],"season_groups":[],"boaters_kimarite_starts":esc.loc[rn].boaters_lane1_starts if j==0 else None,"boaters_escape_rate":esc.loc[rn].boaters_escape_pct if j==0 else None})
            race={"race":int(rn),"eventDay":1,"racers":racers}; X,rr=eng._matrix(race,"2026-11-03"); p2,p3=eng._conditionals(X); p1=eng._first(X,rr,any(int(x["entry_course"])!=int(x["lane"]) for x in racers))
            np.testing.assert_allclose(p1,internal["p1"][int(rn)-1],rtol=0,atol=1e-12)
            np.testing.assert_allclose(p2,internal["p2"][int(rn)-1],rtol=0,atol=1e-12)
            np.testing.assert_allclose(p3,internal["p3"][int(rn)-1],rtol=0,atol=1e-12)
            p=eng.predict(race,"2026-11-03")
            e=expected[expected.race==rn].iloc[0]
            self.assertEqual(";".join(x["combination"] for x in p["tickets"]),e.normal_tickets)
            self.assertEqual(";".join(x["combination"] for x in p["flyPrediction"]["tickets"]),fly.loc[rn].flight_tickets)
            for lane in range(1,7):
                self.assertAlmostEqual(p["win"][str(lane)]/100,e[f"lane{lane}_win"],places=10)
                self.assertAlmostEqual(p["second"][str(lane)]/100,e[f"lane{lane}_second"],places=10)
                self.assertAlmostEqual(p["third"][str(lane)]/100,e[f"lane{lane}_third"],places=10)
if __name__=="__main__": unittest.main()
