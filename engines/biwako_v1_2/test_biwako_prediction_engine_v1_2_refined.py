#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
ENGINE_CANDIDATES = [
    HERE / "biwako_prediction_engine_v1_2_refined.py",
    HERE.parent / "engines" / "biwako_v1_2" / "biwako_prediction_engine_v1_2_refined.py",
]
ENGINE = next((p for p in ENGINE_CANDIDATES if p.exists()), None)
if ENGINE is None:
    pytest.skip("refined engine file not found", allow_module_level=True)

# Import only when the production v1.2 base is present beside/sibling to candidate.
BASE_CANDIDATES = [
    ENGINE.parent / "biwako_prediction_engine_v1_2.py",
    ENGINE.parent.parent / "biwako_v1_2" / "biwako_prediction_engine_v1_2.py",
]
if not any(p.exists() for p in BASE_CANDIDATES):
    pytest.skip("production v1.2 base not available in this test environment", allow_module_level=True)

spec = importlib.util.spec_from_file_location("biwako_v12_refined_testmod", ENGINE)
mod = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mod)
Engine = mod.BiwakoPredictionEngineV12Refined


class S:
    def __init__(self, lane, course):
        self.lane = lane
        self.course = course


def test_refined_constants_are_fixed():
    assert Engine.C1_WIN_PRIOR == pytest.approx(0.52)
    assert Engine.STRONG_ATTACK_SCORE_MIN == pytest.approx(0.27)
    assert Engine.STRONG_ATTACK_PENALTY_MIN == pytest.approx(0.04)
    assert Engine.STRONG_ATTACK_PENALTY_MAX == pytest.approx(0.08)
    assert Engine.SECOND_ACTIVE_BLEND == pytest.approx(0.60)
    assert Engine.THIRD_BLEND_BY_HEAD == {2: 0.35, 3: 0.20, 4: 0.35}
    assert Engine.C1_TICKET_CAP_THRESHOLD == pytest.approx(0.45)
    assert Engine.C1_TICKET_CAP == 2


def test_scenario_score_formula_matches_v12_definition():
    states = [S(i, i) for i in range(1, 7)]
    probs = {
        1: {"win": 0.42, "second": 0.20, "third": 0.10},
        2: {"win": 0.18, "second": 0.25, "third": 0.18},
        3: {"win": 0.22, "second": 0.24, "third": 0.20},
        4: {"win": 0.10, "second": 0.15, "third": 0.22},
        5: {"win": 0.06, "second": 0.10, "third": 0.18},
        6: {"win": 0.02, "second": 0.06, "third": 0.12},
    }
    got = Engine._scenario_raw_scores(probs, states)
    assert got[2] == pytest.approx(1.08 * 0.18 + 0.32 * 0.20)
    assert got[3] == pytest.approx(1.05 * 0.22 + 0.38 * 0.24)
    assert got[4] == pytest.approx(1.08 * 0.10 + 0.40 * 0.15)


def test_source_contract_forbids_odds_and_results_as_prediction_inputs():
    src = ENGINE.read_text(encoding="utf-8")
    # Metadata is allowed to mention these words. Direct race.get(...) access is not.
    forbidden = (
        'race.get("odds")',
        "race.get('odds')",
        'race["odds"]',
        "race['odds']",
        'race.get("result")',
        "race.get('result')",
        'race["result"]',
        "race['result']",
        'race.get("finish")',
        "race.get('finish')",
    )
    for needle in forbidden:
        assert needle not in src


def test_candidate_version_is_not_production_v12_id():
    # Safe rollout: refined candidate must be distinguishable until comparison passes.
    assert Engine.ENGINE_VERSION == "biwako_engine_v1.2_refined"
    assert Engine.PARAMETER_VERSION.startswith("biwako_v1.2_refined_")


def test_strong_attack_reassesses_c1_and_only_redistributes_to_c2_c4(monkeypatch):
    states = [S(i, i) for i in range(1, 7)]
    original = {
        1: {"win": 0.42, "second": 0.20, "third": 0.10},
        2: {"win": 0.18, "second": 0.25, "third": 0.18},
        3: {"win": 0.22, "second": 0.24, "third": 0.20},
        4: {"win": 0.10, "second": 0.15, "third": 0.22},
        5: {"win": 0.06, "second": 0.10, "third": 0.18},
        6: {"win": 0.02, "second": 0.06, "third": 0.12},
    }
    monkeypatch.setattr(
        mod.base12.BiwakoPredictionEngineV12,
        "_marginals",
        lambda _self, _states: {
            lane: values.copy() for lane, values in original.items()
        },
    )
    engine = object.__new__(Engine)

    got = engine._marginals(states)

    assert engine._refined_active_attack is True
    assert 0.04 <= original[1]["win"] - got[1]["win"] <= 0.08
    assert sum(got[lane]["win"] for lane in range(1, 7)) == pytest.approx(1.0)
    assert got[5]["win"] == pytest.approx(original[5]["win"])
    assert got[6]["win"] == pytest.approx(original[6]["win"])
    assert all(got[lane]["win"] > original[lane]["win"] for lane in (2, 3, 4))


def test_strong_attack_low_c1_caps_c1_head_at_two():
    engine = object.__new__(Engine)
    engine._refined_active_attack = True
    probs = {
        1: {"win": 0.40},
        2: {"win": 0.25},
        3: {"win": 0.18},
        4: {"win": 0.10},
        5: {"win": 0.05},
        6: {"win": 0.02},
    }
    joint = []
    for rank in range(8):
        for head in range(1, 7):
            second = head % 6 + 1
            third = second % 6 + 1
            joint.append(
                {
                    "lanes": (head, second, third),
                    "courses": (head, second, third),
                    "score": probs[head]["win"] / (rank + 1),
                    "ticket": f"{head}-{second}-{third}-{rank}",
                }
            )
    joint.sort(key=lambda row: row["score"], reverse=True)

    slots = engine._head_slot_targets(probs, joint)

    assert slots[1] == 2
    assert sum(slots.values()) == 10
