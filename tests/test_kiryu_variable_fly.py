from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from kiryu_v1_engine import A, B, C, KiryuV1


def index(first: int, second: int, third: int) -> int:
    return int(np.flatnonzero(
        (A == first - 1) & (B == second - 1) & (C == third - 1)
    )[0])


class KiryuVariableFlyTests(unittest.TestCase):
    def scores(self) -> np.ndarray:
        scores = np.linspace(0.01, 0.001, 120)
        for head in range(2, 7):
            for rank, third in enumerate(lane for lane in range(2, 7) if lane != head):
                scores[index(head, 1, third)] = 1.0 - rank / 10
        return scores

    def test_concentrated_heads_use_six_plus_four(self) -> None:
        selected, allocation, heads = KiryuV1.select_fly_indices(
            self.scores(), np.array([.10, .35, .20, .14, .12, .09])
        )
        self.assertEqual(allocation, [6, 4])
        self.assertEqual(heads, [2, 3])
        self.assertEqual([sum(A[selected] == head - 1) for head in heads], [6, 4])

    def test_balanced_boundary_uses_four_plus_four_plus_two(self) -> None:
        selected, allocation, heads = KiryuV1.select_fly_indices(
            self.scores(), np.array([.10, .30, .20, .12, .10, .08])
        )
        self.assertEqual(allocation, [4, 4, 2])
        self.assertEqual(heads, [2, 3, 4])
        self.assertEqual([sum(A[selected] == head - 1) for head in heads], [4, 4, 2])
        self.assertTrue(all(0 in (B[j], C[j]) for j in selected))

    def test_dominant_head_replaces_second_head_reciprocal_ticket(self) -> None:
        selected, allocation, heads = KiryuV1.select_fly_indices(
            self.scores(), np.array([.05, .20, .08, .07, .10, .50])
        )
        self.assertEqual(allocation, [6, 4])
        self.assertEqual(heads, [6, 2])
        self.assertIn(index(2, 6, 1), selected)
        self.assertNotIn(index(2, 3, 1), selected)

    def test_reserve_is_not_used_below_threshold(self) -> None:
        selected, _, heads = KiryuV1.select_fly_indices(
            self.scores(), np.array([.18, .24, .08, .07, .11, .32])
        )
        self.assertEqual(heads[:2], [6, 2])
        self.assertIn(index(2, 3, 1), selected)
        self.assertNotIn(index(2, 6, 1), selected)


if __name__ == "__main__":
    unittest.main()
