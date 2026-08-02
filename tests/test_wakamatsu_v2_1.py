from __future__ import annotations

from copy import deepcopy

from automation.wakamatsu_v2_1_adjustments import apply_v21_adjustments


def sample_result():
    return {
        "probabilities": [
            {"lane": i, "win": w, "second": 1/6, "third": 1/6, "top3": 0.5}
            for i, w in enumerate([0.50, 0.15, 0.12, 0.10, 0.08, 0.05], start=1)
        ]
    }


def sample_input():
    return {
        "tide_type": "中潮",
        "tide_phase": "falling",
        "minutes_to_low_tide": 60,
        "boats": [
            {"lane": 1, "entry_course": 1, "class": "B1", "avg_st": 0.19,
             "start_time": 0.20, "exhibition_score": 0.0, "meeting_runs": []},
            {"lane": 2, "entry_course": 2, "class": "A2", "avg_st": 0.16,
             "start_time": 0.15, "exhibition_score": 0.0, "meeting_runs": []},
            {"lane": 3, "entry_course": 3, "class": "B1", "avg_st": 0.16,
             "start_time": 0.08, "exhibition_score": 0.4, "meeting_runs": []},
            {"lane": 4, "entry_course": 4, "class": "B1", "avg_st": 0.17,
             "start_time": 0.06, "exhibition_score": 0.4, "meeting_runs": []},
            {"lane": 5, "entry_course": 5, "class": "A2", "avg_st": 0.17,
             "start_time": 0.05, "exhibition_score": 0.2, "meeting_runs": []},
            {"lane": 6, "entry_course": 6, "class": "A1", "avg_st": 0.13,
             "start_time": 0.12, "exhibition_score": 0.0, "meeting_runs": []},
        ],
    }


def test_each_finish_probability_sums_to_one():
    output = apply_v21_adjustments(sample_result(), sample_input())
    rows = output["probabilities"]
    for key in ("win", "second", "third"):
        assert abs(sum(row[key] for row in rows) - 1.0) < 1e-9


def test_tide_multiplier_is_capped():
    output = apply_v21_adjustments(sample_result(), sample_input())
    assert output["v21_adjustments"]["tide_multiplier"] == 1.35


def test_course3_to_course4_link_is_created():
    output = apply_v21_adjustments(sample_result(), sample_input())
    links = output["v21_adjustments"]["attack_links"]
    assert any(
        link["attack_course"] == 3 and link["outside_course"] == 4
        for link in links
    )


def test_course4_to_course5_and6_links_are_conditional():
    output = apply_v21_adjustments(sample_result(), sample_input())
    links = output["v21_adjustments"]["attack_links"]
    assert any(link["outside_course"] == 5 for link in links)
    assert any(link["outside_course"] == 6 for link in links)


def test_input_result_is_not_mutated():
    original = sample_result()
    before = deepcopy(original)
    apply_v21_adjustments(original, sample_input())
    assert original == before
