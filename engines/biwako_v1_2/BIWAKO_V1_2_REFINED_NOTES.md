# BIWAKO v1.2 refined candidate

## Purpose
Current v1.2 is preserved. This candidate subclasses it so comparison and rollback stay safe.

## Fixed candidate rules
- C1 normal win prior: 52.0%
- Removed C1 prior mass is proportionally redistributed to C2-C6
- Strong attack activation:
  - C1 win 35-60%
  - at least one C2/C3/C4 win >=15%
  - max(2_sashi, 3_attack, 4_attack) >=0.27
- Additional C1 reassessment: -4 to -8 percentage points
- Redistribution of additional C1 reduction: C2-C4 only, weighted by current head probability x attack score
- Conditional second: 60% on active C2/C3/C4 head branches
- Conditional third: C2 35%, C3 20%, C4 35%
- C1 <45% + strong attack: maximum 2 C1-head tickets
- Odds/results are never prediction inputs

## Safe rollout
Place:
- biwako_prediction_engine_v1_2_refined.py
beside:
- biwako_prediction_engine_v1_2.py

Do not replace current production runner yet.
Run existing v1.2 and refined candidate in parallel for historical/current validation first.

## Required production checks
1. Existing v1.2 regression tests all pass.
2. Refined engine produces exactly six normalized win/second/third marginals.
3. Each position sums to ~1.0.
4. Exactly 10 trifecta tickets are produced.
5. On strong attack + C1<45%, C1-head ticket count <=2.
6. No odds/result fields are read by prediction code.
7. Re-run 2026-08-29 and 2026-08-30 24R comparison.
8. Re-run recent held-out DB metrics before production promotion.
