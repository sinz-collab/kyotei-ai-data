"""Focused invariants for the opt-in Kiryu v2 engine."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'automation'))
from kiryu_v1_engine import A, PERMS, KiryuV1


class KiryuV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine=KiryuV1(v2=True)

    def test_entry_recalculation_changes_features_without_reassigning_boats(self):
        racers=[{'lane':i,'entry_course':i,'player_id':str(4000+i),
                 'motor_no':str(i),'boat_no':str(i),
                 'nat_win':5,'local_win':5,'nat_2':40,'local_2':40,
                 'nat_3':55,'local_3':55,'motor_2':30,'motor_3':50,
                 'avg_st':.16,'season_groups':[]} for i in range(1,7)]
        race={'race':4,'eventDay':2,'racers':racers}
        before=self.engine.normal_joint(race,'2026-11-03')
        race['racers'][1]['entry_course']=3
        race['racers'][2]['entry_course']=2
        after=self.engine.normal_joint(race,'2026-11-03')
        self.assertAlmostEqual(float(after.sum()),1,places=13)
        self.assertFalse(np.allclose(before,after))
        self.assertTrue(all(len(set(row))==3 for row in PERMS[self.engine.select_normal_indices(after)]))

    def test_ten_ticket_selector_does_not_repeat_and_preserves_first_eight(self):
        joint=np.arange(1,121,dtype=float)
        joint/=joint.sum()
        v1=KiryuV1()
        old=v1.select_normal_indices(joint)
        new=self.engine.select_normal_indices(joint)
        self.assertEqual(len(new),10)
        self.assertEqual(len(set(new)),10)
        self.assertEqual(new[:8],old[:8])
        for j in new:
            self.assertTrue(0<=A[j]<6)


if __name__=='__main__':unittest.main()
