# Kiryu LIVE correction integration validation (2026-11-03..05)

## Gates

- Frozen morning C+D formal 6+2+2 reproduction: **36/36 exact**.
- 2026-11-04 R1 is excluded; entry changes are reported separately and both corrections are neutralized.
- All final 120-way distributions are normalized and all requested outputs are written as CSV.
- Production engine/data/site files are not modified by this validation.

## Wind candidate selection (strict pre-deadline 23 races)

| method | hits_622 | hits_top10 | trifecta_LL | Brier120 | second_LL |
|---|---|---|---|---|---|
| baseline | 12/23 | 12/23 | 3.867346408 | 0.960829186 | 1.354576008 |
| straight_second_0.100 | 13/23 | 13/23 | 3.847801188 | 0.958822927 | 1.335030788 |
| straight_second_0.125 | 13/23 | 13/23 | 3.843900796 | 0.958361774 | 1.331130396 |
| straight_second_0.150 | 13/23 | 13/23 | 3.840390678 | 0.957917294 | 1.327620278 |
| composite_all_0.100 | 13/23 | 12/23 | 3.862300058 | 0.959588159 | 1.349065639 |
| composite_all_0.125 | 13/23 | 12/23 | 3.862261104 | 0.959342983 | 1.348148716 |
| composite_all_0.150 | 13/23 | 12/23 | 3.862705384 | 0.959125908 | 1.347415137 |
| composite_all_0.175 | 13/23 | 12/23 | 3.863629807 | 0.958937884 | 1.346864301 |

Predeclared lexicographic rule: trifecta logloss, then 120-class Brier, then second-place conditional logloss. Selected: **straight_second_0.150**.

## Integrated four-way comparison

| scope | method | races | hits_622 | hits_top10 | trifecta_LL | Brier120 |
|---|---|---|---|---|---|---|
| all_35 | baseline | 35 | 18 | 18 | 3.970673108 | 0.966004444 |
| all_35 | left_st_only | 35 | 19 | 19 | 3.958186250 | 0.965124296 |
| all_35 | wind_only | 35 | 18 | 18 | 3.958281573 | 0.964598974 |
| all_35 | wind_then_left_st | 35 | 18 | 18 | 3.945552992 | 0.963664706 |
| normal_entry_29 | baseline | 29 | 14 | 14 | 4.000800548 | 0.966545908 |
| normal_entry_29 | left_st_only | 29 | 15 | 15 | 3.985730202 | 0.965483661 |
| normal_entry_29 | wind_only | 29 | 14 | 14 | 3.985845247 | 0.964849652 |
| normal_entry_29 | wind_then_left_st | 29 | 14 | 14 | 3.970483166 | 0.963722087 |
| entry_changed_6 | baseline | 6 | 4 | 4 | 3.825057149 | 0.963387364 |
| entry_changed_6 | left_st_only | 6 | 4 | 4 | 3.825057149 | 0.963387364 |
| entry_changed_6 | wind_only | 6 | 4 | 4 | 3.825057149 | 0.963387364 |
| entry_changed_6 | wind_then_left_st | 6 | 4 | 4 | 3.825057149 | 0.963387364 |
| strict_predeadline_23 | baseline | 23 | 12 | 12 | 3.867346408 | 0.960829186 |
| strict_predeadline_23 | left_st_only | 23 | 13 | 13 | 3.848495916 | 0.958937852 |
| strict_predeadline_23 | wind_only | 23 | 13 | 13 | 3.840390678 | 0.957917294 |
| strict_predeadline_23 | wind_then_left_st | 23 | 13 | 13 | 3.821329045 | 0.955975155 |

Formal 6+2+2 and simple probability top-10 are deliberately reported in separate columns. Detailed ranked tickets are in `tickets.csv`.

## Reproducibility and production parity

- A second independent validation run produced matching SHA-256 hashes for all 9 deterministic validation outputs.
- Production-vs-validation replay passed for all 36 races. Maximum absolute error was `0` for the baseline 120 probabilities, `5.55e-17` for corrected 120 probabilities, and `2.22e-16` for all 18 lane marginals.
- Formal 6+2+2 tickets, ticket order, hits, and correction apply/stop decisions matched for every race. Details are in `production_parity.json`.
- The frozen fly prediction was unchanged in live-run smoke tests. An entry-change replay stopped both corrections and left all normal probabilities and tickets unchanged.

## Regression

- Kiryu suite: 18 tests passed.
- Morning/publication/live-fetch regression: 73 tests passed, 1 environment-dependent test skipped.

## Provenance limitation

The 2026-11-03 input documents are retrospective Git artifacts and lack pre-deadline capture proof. They are included because the requested range is November 3-5, but they are not relabeled as contemporaneous LIVE data. November 4-5 retain the existing timestamp audit; November 4 R1 remains excluded.
