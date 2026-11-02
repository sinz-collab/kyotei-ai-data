# Heiwajima Fly v6 candidate (validation only)

This directory is isolated from the production engine and publication path.

The candidate fly probability uses exactly six clipped features listed in
`model.py`. Missing inputs become neutral `0` and are recorded in `missing`.
The logistic coefficients, 0.50 decision threshold, 0.50/0.25/0.25 fly-ticket
weights, score ordering, and lexicographic tie break are frozen constants.
Tide, wind, odds, result, payout and saved prediction fields are not accepted
by the model functions.

Normal tickets are the first 12 of the existing v2.7 120-way score
`(P1 * P2 * P3) ** 0.97`. Fly tickets exclude lane 1 as head and merge head
balance, v2.7 conditional placement, and attack-scenario support. Normal/fly
overlap is intentionally retained.

`replay_77r.py` builds v2.7 inputs from morning entries plus pre-race live
files, runs inference before reading the actual trifecta, and uses the actual
only in the final scoring pass. It never reads stored tickets/probabilities.

