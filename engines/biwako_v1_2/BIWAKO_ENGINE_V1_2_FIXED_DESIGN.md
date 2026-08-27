# BIWAKO ENGINE v1.2 — FIXED DESIGN 2026-08-27

## Purpose
This file freezes the v1.2 rules agreed during 2026-08-26 validation.
Do not tune from the 8/26 results. Odds and results are prohibited prediction inputs.

## Base
`biwako_prediction_engine_v1_2.py` subclasses production v1.1.
Do not rewrite the v1.1 probability pipeline.

## Fixed probability changes
1. Motor pre-final signal: cumulative 70% + recent10 30%.
2. Day1 motor factor: 0.70. Setsukan remains 0.20.
3. Attack-defense includes C5/C6 with distance weights C5=.55, C6=.35.
4. Weak-C1 multi-attack gate:
   - C1 escape < .48
   - vulnerability(sashi+makuri+makurizashi allowed) >= .35
   - at least two C2-C4 attackers raw score >= .01
   - C1 head penalty min .20 log, max .24 log
   - 85% redistributed to matching attackers.
5. Conditional second current-layout blend: .30/.18/.08 at N>=100/30/10.
6. C4 attack -> C5 follow second link = .09 * live link, only when:
   - C4 is actually a slit attack course
   - C5 has live link > 0
   - C5's already-adjusted second+third baseline is at least field median.
7. Existing v1.1 conditional third DB/branch logic remains.

## Ticket generation
Exactly 10 unique trifectas.

### Base head slots
- head >=55%: 6
- 45-54.9%: 5
- 35-44.9%: 4
- 25-34.9%: 3
- 20-24.9%: 2
- 8-19.9%: 1
- <8%: 0

### Diversity allocation
- A 20-24.9% head gets one extra slot first when it has >=3 positive scenario candidates.
- If slots remain and top head >=55%, top head gets one extra.
- Any remaining slot is assigned by next-best joint/scenario score.

This reproduces the frozen shapes used in review:
- 6R: 1=4, 4=3, 3=1, 5=1, 2=1
- 7R: 1=7, 2=1, 6=1, 3=1
- 8R: 1=5, 2=3, 5=1, 3=1

### Strong-axis scenario hedge
Maximum one ticket.
Conditions:
- top head >=45%
- top head has >=5 slots
- C5 or C6 is a clear outer slit attacker (>= attack_margin faster than direct inner)
- that outer attacker itself has head probability <15%

Hedge scenario:
- C6 attack -> retain C5/C4 inside pair under strong head
- C5 attack -> retain C4/C3 inside pair under strong head
- choose better order by joint score
- candidate must be >=8% of weakest selected same-head ticket score
- replace at most one standard same-head ticket

The hedge changes ticket selection only. It does not raise marginal probabilities.

## Hard prohibitions
- no odds input
- no result input
- no same-day result trend
- no post-result tuning
- no forced longshot/upset quota
- no raw exhibition-ST coefficient that directly dominates probabilities
