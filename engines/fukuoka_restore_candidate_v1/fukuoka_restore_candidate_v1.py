from __future__ import annotations

from itertools import permutations
from typing import Any

HEAD_SCENARIO_MULTIPLIER = 3.0

def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v in (None, "", "-"):
            return default
        return float(v)
    except Exception:
        return default

def _normalize(values: dict[int, float], excluded: set[int] | None = None) -> dict[int, float]:
    excluded = excluded or set()
    raw = {k: (0.0 if k in excluded else max(float(v), 1e-9)) for k, v in values.items()}
    total = sum(raw.values())
    return {k: (0.0 if k in excluded else raw[k] / total) for k in raw}

def _live_entries(race: dict, kind: str) -> list[dict]:
    live = race.get("live") or {}
    if kind in live and isinstance(live[kind], dict):
        rows = live[kind].get("entries") or []
        if rows:
            return rows
    if kind == "exhibition":
        return ((live.get("direct") or {}).get("racers") or [])
    if kind == "original":
        for key in ("original", "original_exhibition"):
            rows = ((live.get(key) or {}).get("entries") or [])
            if rows:
                return rows
    return []

def _features(race: dict) -> dict[int, dict]:
    racers = {int(x["lane"]): x for x in race["racers"]}
    base = race.get("prediction") or race.get("predictionFinal") or {}
    win = base.get("win") or {}
    second = base.get("second") or {}
    third = base.get("third") or {}

    exhibition = {
        int(x["lane"]): x
        for x in _live_entries(race, "exhibition")
        if x.get("lane") is not None
    }
    original = {
        int(x["lane"]): x
        for x in _live_entries(race, "original")
        if x.get("lane") is not None
    }

    f: dict[int, dict] = {}
    for lane, racer in racers.items():
        motor = racer.get("motor_recent") or {}
        ex = exhibition.get(lane) or {}
        ori = original.get(lane) or {}
        f[lane] = {
            "p1": _num(win.get(str(lane))),
            "p2": _num(second.get(str(lane))),
            "p3": _num(third.get(str(lane))),
            "local": _num(racer.get("local_win")),
            "local_st": _num(racer.get("boaters_local_avg_st") or racer.get("local_st"), 0.18),
            "sashi": _num(racer.get("boaters_sashi_rate")),
            "makuri": _num(racer.get("boaters_makuri_rate")),
            "makuri_sashi": _num(racer.get("boaters_makuri_sashi_rate")),
            "escape": _num(racer.get("boaters_escape_rate")),
            "motor_top2": _num(motor.get("top2_rate")),
            "motor_top3": _num(motor.get("top3_rate")),
            "motor_trend": motor.get("trend"),
            "start_rank": _num(ex.get("start_rank"), 99),
            "turn": _num(ori.get("turn_time"), 99),
            "straight": _num(ori.get("straight_time"), 99),
            "lap": _num(ori.get("lap_time"), 99),
            "sum": _num(ori.get("sum"), 99),
        }

    for key in ("turn", "straight", "lap", "sum"):
        ordered = sorted((f[l][key], l) for l in f if f[l][key] < 90)
        rank = {lane: idx + 1 for idx, (_, lane) in enumerate(ordered)}
        for lane in f:
            f[lane][f"{key}_rank"] = rank.get(lane, 99)

    return f

def predict_restored(race: dict) -> dict:
    """
    福岡9割版の判断構造を復元するシナリオ層。
    結果・オッズは使用しない。
    現行v1.0のP1/P2/P3を土台に、
    頭シナリオ → 頭別P2 → 頭×2着別P3 を再正規化する。
    """
    f = _features(race)
    escape1 = f[1]["escape"]

    # --- 頭シナリオ成立 ---
    strong3 = f[3]["makuri"] >= 20.0 and f[3]["local"] >= 4.5

    strong4 = (
        (escape1 <= 25.0 and f[4]["local"] >= 5.3)
        or (f[4]["local"] >= 7.2 and f[4]["local_st"] <= 0.14)
        or (f[4]["p1"] >= 11.5 and f[4]["local_st"] <= 0.14)
    )

    # 3攻めが明確なら4攻めより3を優先。
    strong2 = (
        f[2]["p1"] >= 17.0
        and f[2]["local_st"] <= 0.17
        and (f[2]["sashi"] >= 15.0 or f[2]["local"] >= 6.0 or escape1 <= 10.0)
    )

    strong6 = (
        f[6]["local"] >= 7.0
        and f[6]["motor_trend"] == "up"
        and f[6]["straight_rank"] <= 2
    )

    p1_mult = {lane: 1.0 for lane in range(1, 7)}
    if strong3:
        p1_mult[3] *= HEAD_SCENARIO_MULTIPLIER
    elif strong4:
        p1_mult[4] *= HEAD_SCENARIO_MULTIPLIER
    if strong2:
        p1_mult[2] *= HEAD_SCENARIO_MULTIPLIER
    if strong6:
        p1_mult[6] *= HEAD_SCENARIO_MULTIPLIER

    p1 = _normalize({lane: f[lane]["p1"] * p1_mult[lane] for lane in range(1, 7)})

    scored = []
    conditional_audit = []

    for head, second, third in permutations(range(1, 7), 3):
        second_mult = {lane: 1.0 for lane in range(1, 7)}

        if head == 2:
            second_mult[1] *= 1.50
            second_mult[4] *= 1.20
            second_mult[5] *= 1.20

            two_to_six = (
                f[2]["p1"] >= 17.0
                and 25.0 <= escape1 <= 50.0
                and (f[6]["turn_rank"] <= 2 or f[6]["straight_rank"] <= 2)
            )
            if two_to_six:
                second_mult[6] *= 2.50

        elif head == 3:
            second_mult[1] *= 1.50
            second_mult[4] *= 1.35
            second_mult[5] *= 1.15

        elif head == 4:
            second_mult[1] *= 1.55
            second_mult[5] *= 1.35
            second_mult[6] *= 1.18

            four_to_six = (
                f[4]["p1"] >= 8.0
                and f[6]["motor_top3"] >= 70.0
                and f[6]["motor_trend"] == "up"
                and f[6]["start_rank"] <= 2
                and (f[6]["turn_rank"] <= 2 or f[6]["straight_rank"] <= 2)
            )
            if four_to_six:
                second_mult[6] *= 2.60

        elif head == 1:
            # 1逃げでも5/6が実戦足・外連動で2着まで上がるケースを残す。
            for lane in (5, 6):
                if f[lane]["p2"] >= 10.0 and (
                    f[lane]["motor_top3"] >= 60.0 or f[lane]["straight_rank"] <= 2
                ):
                    second_mult[lane] *= 1.70

        p2 = _normalize(
            {lane: f[lane]["p2"] * second_mult[lane] for lane in range(1, 7)},
            {head},
        )

        third_mult = {lane: 1.0 for lane in range(1, 7)}

        if head == 2 and second == 1:
            third_mult[3] *= 1.25
            third_mult[4] *= 1.20
            third_mult[5] *= 1.15
            third_mult[6] *= 1.12

        if head == 2 and second == 6:
            third_mult[3] *= 1.80
            third_mult[1] *= 1.60

        if head == 3 and second == 1:
            third_mult[4] *= 1.70
            third_mult[5] *= 1.30

        if head == 4 and second == 1:
            third_mult[5] *= 1.70
            third_mult[6] *= 1.45
            third_mult[3] *= 1.30
            third_mult[2] *= 1.18

        if head == 4 and second == 6:
            third_mult[1] *= 2.00

        if head == 1 and second in (5, 6):
            third_mult[3] *= 1.60
            third_mult[4] *= 1.25

        p3 = _normalize(
            {lane: f[lane]["p3"] * third_mult[lane] for lane in range(1, 7)},
            {head, second},
        )

        score = p1[head] * p2[second] * p3[third]
        scored.append((score, head, second, third))

    scored.sort(reverse=True)
    top10 = scored[:10]

    tickets = []
    for idx, (score, h, s, t) in enumerate(top10):
        role = "本線" if idx < 6 else ("ずらし" if idx < 8 else "穴")
        tickets.append({
            "combo": f"{h}-{s}-{t}",
            "score": round(score, 8),
            "role": role,
        })

    return {
        "engine": "fukuoka_restore_candidate_v1",
        "result_used": False,
        "odds_used": False,
        "head_scenarios": {
            "strong2": strong2,
            "strong3": strong3,
            "strong4": strong4,
            "strong6": strong6,
        },
        "p1": {str(k): round(v * 100, 4) for k, v in p1.items()},
        "tickets": tickets,
        "Main6": [x["combo"] for x in tickets[:6]],
        "Zure2": [x["combo"] for x in tickets[6:8]],
        "Ana2": [x["combo"] for x in tickets[8:10]],
    }
