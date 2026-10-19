from copy import deepcopy

from ashiya_engine.exhibition_v51 import (
    apply_to_head_conditionals,
    build_signals,
    enumerate_joint,
    signal,
)


def racers():
    return [
        {"lane": 1, "actual_course": 1, "lap_time": 37.50, "turn_time": 7.90},
        {"lane": 2, "actual_course": 2, "lap_time": 37.25, "turn_time": 7.70},
        {"lane": 3, "actual_course": 3, "lap_time": 37.40, "turn_time": 7.80},
        {"lane": 4, "actual_course": 4, "lap_time": 37.10, "turn_time": 7.60},
        {"lane": 5, "actual_course": 5, "lap_time": 37.20, "turn_time": 7.65},
        {"lane": 6, "actual_course": 6, "lap_time": 37.00, "turn_time": 7.45},
    ]


def conditionals():
    return {
        head: {
            "second": [0.0 if lane == head else 0.2 for lane in range(1, 7)],
            "third": [0.0 if lane == head else 0.2 for lane in range(1, 7)],
            "scenario_audit": {},
        }
        for head in range(1, 7)
    }


def test_signal_and_actual_course_adjacency():
    assert signal(0.10) == 0.0
    assert round(signal(0.25), 10) == 1.0
    rows = racers()
    rows[1]["actual_course"], rows[2]["actual_course"] = 3, 2
    signals, reason = build_signals(rows)
    assert reason is None
    # Lane 3 moved to course 2 and is compared with course 1, not lane 2.
    assert signals[3]["lap_delta"] == 0.10
    assert signals[2]["actual_course"] == 3


def test_disabled_mode_is_bit_for_bit_conditional_fallback():
    original = conditionals()
    adjusted, audit = apply_to_head_conditionals(deepcopy(original), racers())
    assert adjusted == original
    assert audit["status"] == "disabled_unvalidated"
    assert audit["productionAdopted"] is False


def test_combined_research_mode_keeps_head_and_120_way_consistency():
    adjusted, audit = apply_to_head_conditionals(conditionals(), racers(), "combined")
    assert audit["status"] == "research_applied"
    assert all(abs(sum(adjusted[head][place]) - 1.0) < 1e-12 for head in range(1, 7) for place in ("second", "third"))
    assert all(adjusted[head][place][head - 1] == 0.0 for head in range(1, 7) for place in ("second", "third"))
    win = [.36, .18, .14, .12, .11, .09]
    rows, consistency = enumerate_joint(win, adjusted)
    assert len(rows) == 120
    assert consistency["sumValid"] is True
    assert consistency["firstMarginalsValid"] is True


def test_missing_time_never_becomes_zero_second_bonus():
    rows = racers()
    rows[3]["lap_time"] = 0
    signals, reason = build_signals(rows)
    assert reason is None
    assert signals[4]["status"] == "missing_time"
    assert signals[4]["attack"] == 0.0
    assert signals[4]["chase"] == 0.0
