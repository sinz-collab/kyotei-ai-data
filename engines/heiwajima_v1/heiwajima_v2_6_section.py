from __future__ import annotations

import math
from typing import Any

ENGINE_ID = "heiwajima_complete_v2_6_20260829"


def _num(value: Any, default=None):
    try:
        if value in (None, "", "-"):
            return default
        text = str(value).strip().replace("F", "-")
        parsed = float(text)
        return default if math.isnan(parsed) else parsed
    except (TypeError, ValueError):
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _finish_int(value: Any):
    if value in (None, "", "-"):
        return None
    text = str(value).strip().replace("着", "")
    if not text.isdigit():
        return None
    n = int(text)
    return n if 1 <= n <= 6 else None


# Completion-weighted meeting progression.  The value is not a direct probability
# weight; it controls the bounded logit contribution of current-meeting evidence.
SECTION_PROGRESS_BY_RUNS = {
    0: 0.00,
    1: 0.15,
    2: 0.25,
    3: 0.35,
    4: 0.45,
    5: 0.55,
}
SECTION_PROGRESS_MAX = 0.625

# Pure finishing-quality component. Course difficulty is handled separately.
FINISH_SCORE = {1: 1.00, 2: 0.60, 3: 0.25, 4: -0.05, 5: -0.30, 6: -0.55}

# Outside courses receive credit for producing the same finish from a harder
# starting position; inside under-performance is penalised more heavily.
COURSE_VALUE = {1: -0.25, 2: -0.12, 3: 0.00, 4: 0.10, 5: 0.18, 6: 0.25}

# Section contribution is logit-space and later re-normalised by the core engine.
WIN_LOGIT_CAP = {1: 0.28, 2: 0.30, 3: 0.34, 4: 0.38, 5: 0.45, 6: 0.50}
SECOND_LOGIT_CAP = {1: 0.20, 2: 0.22, 3: 0.24, 4: 0.27, 5: 0.30, 6: 0.34}
THIRD_LOGIT_CAP = {1: 0.15, 2: 0.17, 3: 0.19, 4: 0.22, 5: 0.25, 6: 0.29}


def section_progress_weight(runs_completed: int) -> float:
    n = max(0, int(runs_completed or 0))
    if n >= 6:
        return SECTION_PROGRESS_MAX
    return SECTION_PROGRESS_BY_RUNS[n]


def _current_meeting_motor_rows(boat: dict) -> list[dict]:
    """Return motor_recent rows that belong to the current racer.

    BOATERS motor_recent10 commonly includes the current meeting's completed
    races at the head of the list. Matching by racer name avoids treating prior
    users of the same motor as current-meeting evidence.
    """
    recent = boat.get("motor_recent") or {}
    player = str(boat.get("player_name") or boat.get("name") or "").replace(" ", "")
    if not player:
        return []
    out = []
    for row in recent.get("runs") or []:
        racer = str(row.get("racer") or "").replace(" ", "")
        if racer == player:
            out.append(row)
    return out


def _match_motor_rank(run: dict, motor_rows: list[dict], used: set[int]):
    course = int(_num(run.get("course") or run.get("actual_course") or run.get("entry_course"), 0) or 0)
    finish = _finish_int(run.get("finish"))
    best = None
    for idx, row in enumerate(motor_rows):
        if idx in used:
            continue
        row_course = int(_num(row.get("course"), 0) or 0)
        row_finish = _finish_int(row.get("finish"))
        if course and row_course != course:
            continue
        if finish is not None and row_finish is not None and row_finish != finish:
            continue
        rank = _num(row.get("exhibition_rank"))
        if rank is None:
            continue
        best = (idx, rank)
        break
    if best is not None:
        used.add(best[0])
        return best[1]
    return None


def _run_components(run: dict, motor_rank=None) -> dict:
    course = int(_num(run.get("course") or run.get("actual_course") or run.get("entry_course"), 0) or 0)
    finish = _finish_int(run.get("finish"))
    st = _num(run.get("st"))

    if course not in range(1, 7) or finish is None:
        return {"valid": False}

    finish_component = FINISH_SCORE[finish]

    # Course value matters most when the result is meaningful.  Top-3 from an
    # outer course gets full credit; 4th gets partial credit; 5th/6th gets only
    # a small cushioning effect rather than an automatic positive correction.
    result_gate = {1: 1.00, 2: 1.00, 3: 0.90, 4: 0.55, 5: 0.30, 6: 0.15}[finish]
    course_component = COURSE_VALUE[course] * result_gate

    st_component = 0.0 if st is None else _clip((0.17 - st) / 0.10, -1.0, 1.0)
    motor_component = 0.0 if motor_rank is None else _clip((4.0 - motor_rank) / 3.0, -1.0, 1.0)

    # Frozen design requested in the v2.6 review:
    # finish 40%, race ST 20%, actual-course value 20%, current-meeting motor fit 20%.
    score = (
        finish_component * 0.40
        + st_component * 0.20
        + course_component * 0.20
        + motor_component * 0.20
    )
    return {
        "valid": True,
        "score": _clip(score, -1.0, 1.0),
        "finish": finish_component,
        "race_st": st_component,
        "course_value": course_component,
        "motor_fit": motor_component,
        "course": course,
        "finish_raw": finish,
        "st_raw": st,
        "motor_rank": motor_rank,
    }


def _trend_score(run_scores: list[float]) -> dict:
    n = len(run_scores)
    if n < 2:
        return {"score": 0.0, "label": "insufficient", "reliability": 0.0}

    # End-to-end slope with reliability shrinkage. Two runs may indicate a
    # direction but must not dominate the base model.
    raw = run_scores[-1] - run_scores[0]
    reliability = {2: 0.55, 3: 0.75, 4: 0.90}.get(n, 1.0)
    trend = _clip(raw * 0.30 * reliability, -0.20, 0.20)
    if trend >= 0.07:
        label = "up"
    elif trend <= -0.07:
        label = "down"
    else:
        label = "flat"
    return {"score": trend, "label": label, "reliability": reliability}


def section_progression_adjustment(boat: dict, course: int, day_bucket: str = "middle") -> dict:
    runs = list(boat.get("season_runs") or boat.get("setsukan_runs") or [])
    motor_rows = _current_meeting_motor_rows(boat)
    used_motor_rows: set[int] = set()

    components = []
    for run in runs:
        motor_rank = _match_motor_rank(run, motor_rows, used_motor_rows)
        comp = _run_components(run, motor_rank=motor_rank)
        if comp.get("valid"):
            components.append(comp)

    n = len(components)
    progress = section_progress_weight(n)
    if n == 0 or progress <= 0.0:
        return {
            "win": 0.0,
            "second": 0.0,
            "third": 0.0,
            "section_index": 0.0,
            "progress_weight": 0.0,
            "runs_completed": 0,
            "trend": "insufficient",
            "trend_score": 0.0,
            "motor_prior_scale": 1.0,
            "run_components": [],
        }

    base_index = sum(x["score"] for x in components) / n
    trend = _trend_score([x["score"] for x in components])
    section_index = _clip(base_index + trend["score"], -1.0, 1.0)

    # Late-meeting section data should replace some prior motor_recent weight,
    # not merely stack on top of it. This prevents double counting.
    motor_prior_scale = 1.0 - min(0.40, progress * 0.60)

    c = int(course)
    win = _clip(section_index * progress * 1.50, -WIN_LOGIT_CAP.get(c, 0.32), WIN_LOGIT_CAP.get(c, 0.32))
    second = _clip(section_index * progress * 0.90, -SECOND_LOGIT_CAP.get(c, 0.24), SECOND_LOGIT_CAP.get(c, 0.24))
    third = _clip(section_index * progress * 0.60, -THIRD_LOGIT_CAP.get(c, 0.20), THIRD_LOGIT_CAP.get(c, 0.20))

    # A single completed race is low reliability: keep head movement modest and
    # let second/third reflect useful outer-course performance first.
    if n == 1:
        win *= 0.72
        second *= 0.88
        third *= 0.95

    return {
        "win": win,
        "second": second,
        "third": third,
        "section_index": section_index,
        "base_index": base_index,
        "progress_weight": progress,
        "runs_completed": n,
        "trend": trend["label"],
        "trend_score": trend["score"],
        "trend_reliability": trend["reliability"],
        "motor_prior_scale": motor_prior_scale,
        "run_components": components,
    }
