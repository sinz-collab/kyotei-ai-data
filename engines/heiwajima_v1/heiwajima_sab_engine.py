def judge_sab(boats, scenarios, completeness, ticket_count=None):
    win = sorted([b["win_prob"] for b in boats], reverse=True)
    axis_gap = win[0] - win[1]
    top_s = scenarios[0]["probability"] if scenarios else 0

    missing_codes = completeness.get("missing_codes", [])

    # 展示前pendingと、選手DBの部分欠損は重大欠損として一律加算しない。
    ignored_partial_codes = {
        "original_exhibition_pending",
        "exhibition_pending",
        "player_course_missing",
        "player_lane_missing",
        "player_id_unresolved",
    }

    major_missing = sum(
        1
        for code in missing_codes
        if code not in ignored_partial_codes
    )

    course_reflected = int(
        completeness.get("player_course_reflected", 0)
    )
    lane_reflected = int(
        completeness.get("player_lane_reflected", 0)
    )
    local_st_reflected = int(
        completeness.get("local_st_reflected", 0)
    )

    course_lane_coverage = min(
        course_reflected,
        lane_reflected,
    ) / 6.0

    local_st_coverage = local_st_reflected / 6.0

    # コース・枠別DBを主軸、当地STを補助としてデータ充足度を算出。
    coverage = (
        course_lane_coverage * 0.80
        + local_st_coverage * 0.20
    )

    entry_changed = bool(
        completeness.get("entry_changed", False)
    )

    # SABは予想ロジックの再現性評価。
    # 買い目数・表示点数・金額配分は判定に使用しない。
    if (
        axis_gap >= 0.25
        and top_s >= 0.28
        and major_missing == 0
        and coverage >= 0.80
        and not entry_changed
    ):
        grade = "S"
    elif (
        axis_gap >= 0.12
        and top_s >= 0.18
        and major_missing <= 1
        and coverage >= 0.50
    ):
        grade = "A"
    else:
        grade = "B"

    raw_confidence = round(
        35
        + axis_gap * 90
        + top_s * 45
        + coverage * 15
        - major_missing * 10
        - (6 if entry_changed else 0),
        1,
    )

    grade_cap = {
        "S": 100.0,
        "A": 89.9,
        "B": 69.9,
    }[grade]

    confidence = max(
        0.0,
        min(grade_cap, raw_confidence),
    )

    return {
        "grade": grade,
        "confidence": confidence,
        "axis_gap": round(axis_gap, 4),
        "top_scenario_probability": round(top_s, 4),
        "master_coverage": round(coverage, 4),
        "entry_changed": entry_changed,
        "ticket_count_used": False,
    }
