from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from kiryu_live_corrections import apply_live_corrections  # noqa: E402
from kiryu_v1_engine import A, B, C  # noqa: E402


def racers():
    return [
        {"lane": lane, "avg_st": value, "local_st": None}
        for lane, value in enumerate((0.16, 0.18, 0.12, 0.17, 0.14, 0.20), 1)
    ]


def live_input(entry=None):
    return {
        "actual_entry": entry or list(range(1, 7)),
        "boats": [
            {"lane": lane, "straight_time": value}
            for lane, value in enumerate((7.10, 7.05, 7.16, 7.00, 7.12, 7.08), 1)
        ],
        "weather": {"wind_speed": 2.0},
    }


class KiryuLiveCorrectionTests(unittest.TestCase):
    def test_outer_third_live_preserves_first_and_second_joint(self) -> None:
        before=np.arange(1,121,dtype=float)
        before/=before.sum()
        value=live_input()
        for index,boat in enumerate(value['boats']):
            boat.update({'half_lap_time':18.8+index*.12,
                         'turn_time':5.1+index*.08,
                         'exhibition_time':6.6+index*.03})
        enriched=[]
        for racer in racers():
            enriched.append(dict(racer,nat_3=45,local_3=40,motor_3=52))
        after,audit=apply_live_corrections(before,enriched,value)
        self.assertTrue(audit['outerThirdLive']['applied'])
        self.assertTrue(np.isfinite(after).all())
        self.assertAlmostEqual(float(after.sum()),1,places=13)
        self.assertTrue(any(abs(audit['outerThirdLive'][k])>0 for k in
                            ('lane5LogOddsDelta','lane6LogOddsDelta')))
        # The existing second-place model is active, so compare to its
        # output without the new third-only score using the captured audit.
        from kiryu_live_corrections import _apply_second_score, _straight_score, _national_st_wall
        old=_apply_second_score(before,.15*_straight_score(value['boats']))
        _,wall=_national_st_wall(enriched)
        old=_apply_second_score(old,.10*wall)
        for head in range(6):
            for second in range(6):
                pair=(A==head)&(B==second)
                self.assertAlmostEqual(float(old[pair].sum()),float(after[pair].sum()),places=13)

    def test_corrections_only_change_second_conditionals(self) -> None:
        base = np.full(120, 1 / 120)
        corrected, audit = apply_live_corrections(base, racers(), live_input())
        self.assertAlmostEqual(float(corrected.sum()), 1.0, places=14)
        self.assertTrue(audit["windStraight"]["applied"])
        self.assertTrue(audit["leftNeighborSt"]["applied"])
        for lane in range(6):
            self.assertAlmostEqual(base[A == lane].sum(), corrected[A == lane].sum(), places=14)
        for first in range(6):
            for second in range(6):
                if first == second:
                    continue
                before = base[(A == first) & (B == second)]
                after = corrected[(A == first) & (B == second)]
                self.assertTrue(np.allclose(before / before.sum(), after / after.sum()))

    def test_entry_change_stops_both_corrections(self) -> None:
        base = np.arange(1, 121, dtype=float)
        base /= base.sum()
        corrected, audit = apply_live_corrections(base, racers(), live_input([1, 3, 2, 4, 5, 6]))
        self.assertTrue(np.array_equal(base, corrected))
        self.assertTrue(audit["stoppedForEntryChange"])
        self.assertFalse(audit["windStraight"]["applied"])
        self.assertFalse(audit["leftNeighborSt"]["applied"])

    def test_missing_national_st_fails_closed(self) -> None:
        missing = [{"lane": lane, "avg_st": None} for lane in range(1, 7)]
        with self.assertRaisesRegex(RuntimeError, "national average ST"):
            apply_live_corrections(np.full(120, 1 / 120), missing, live_input())

    def test_missing_straight_time_fails_closed(self) -> None:
        value = live_input()
        value["boats"][2]["straight_time"] = None
        with self.assertRaisesRegex(RuntimeError, "straight times"):
            apply_live_corrections(np.full(120, 1 / 120), racers(), value)


if __name__ == "__main__":
    unittest.main()
