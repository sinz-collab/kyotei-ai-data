from __future__ import annotations


CLASS_MULTIPLIER = {
    "A1": 1.20,
    "A2": 1.10,
    "B1": 1.00,
    "B2": 0.88,
}

FIRST_COURSE_MULTIPLIER = {
    1: 1.00,
    2: 1.00,
    3: 1.05,
    4: 1.08,
    5: 0.92,
    6: 0.78,
}

THIRD_COURSE_MULTIPLIER = {
    1: 0.85,
    2: 0.95,
    3: 1.00,
    4: 1.05,
    5: 1.12,
    6: 1.20,
}


def class_motor_multipliers(boat, course, day_bucket):
    """初日は選手格によるモーター引き出し能力を加味する。"""
    if day_bucket != "early":
        return {"win": 1.0, "second": 1.0, "third": 1.0}

    class_mult = CLASS_MULTIPLIER.get(str(boat.get("class") or "B1"), 1.0)
    return {
        "win": class_mult * FIRST_COURSE_MULTIPLIER.get(int(course), 1.0),
        "second": class_mult * 1.00,
        "third": class_mult * THIRD_COURSE_MULTIPLIER.get(int(course), 1.0),
    }
