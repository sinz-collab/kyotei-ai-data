# Shimonoseki v6.0 fix1

Implemented after code audit:

1. Precise tide / water surface layer
   - final receives `direct` and `tide_events`.
   - precise tide factors are applied once, after actual-course remap and live/interaction layers.
   - tide factors are course-based and applied to the boat occupying that actual course.
   - generic rough water only shrinks confidence weakly toward uniform; no unverified directional bias is invented.
   - the documented final-day NW 4m+/wave 4cm+ outside-pick residual is only triggered when a textual NW direction is available.

2. SAB
   - 100 point coherence score: axis 25 / opponent 20 / scenario 20 / data 20 / ticket 15.
   - penalties include entry change, rough water and multi-head ambiguity.
   - S >= 80, A >= 65, B >= 50, otherwise 見.

3. 10 tickets
   - main 6 + deviation 2 + scenario-conditioned upset 2.
   - all 10 unique.
   - upset head uses escape/attack interaction first, then strongest compound attacker, otherwise alternate probability head.

4. Audit correction retained
   - compound attack requires `attack_rate > 0`; motor/series/slit/SUM alignment alone cannot create an attack residual.

No result or odds fields are used in probability, SAB or ticket calculations.
