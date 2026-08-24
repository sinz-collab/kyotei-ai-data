# Validation status

## Completed in this build
- Python compile: PASS
- 1st/2nd/3rd probability normalization: PASS
- 10 unique trifecta tickets: PASS
- `result` leakage invariance: PASS
- `odds` leakage invariance: PASS
- 6 boats × probabilityReview fields: PASS
- delta exact arithmetic: PASS
- 2026-08-23 R4: `1-6-3` in 10 tickets: PASS
- 2026-08-23 R5: `1-3-6` in 10 tickets: PASS
- 2026-08-23 R7: `1-4-6` in main 6: PASS
- R4/R5/R7 SAB: A/A/A

## Not falsely claimed
A full 2026-08-23 12R re-run and multi-day chronological calibration are not marked complete in this package because all historical input payloads were not locally mounted in this build environment.
Codex must run the supplied engine against repository/VPS historical inputs before production cutover.

## Required cutover gate
Do not retire v6.0 until:
1. 12R comparison is complete.
2. Existing-venue regression tests pass.
3. motor recent10 builder/master are migrated unchanged.
4. site browser double-correction is disabled for `shimonoseki`.
5. live final JSON contains `probabilityReview` for all six boats.
