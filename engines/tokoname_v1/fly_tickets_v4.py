from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

MODEL_VERSION = "tokoname_fly_tickets_v4"
STAGE = "post_exhibition"
ARTIFACT_DIRECTORY = "tokoname_fly_tickets_v4"
LANES = tuple(range(1, 7))


class FlyTicketsUnavailable(ValueError):
    pass


def waiting_prediction(reason: str = "exhibition_required") -> dict:
    return {
        "status": "waiting",
        "reason": reason,
        "modelVersion": MODEL_VERSION,
        "stage": "waiting_for_exhibition",
        "condition": "lane1_not_first",
        "tickets": [],
    }


def unavailable_prediction(reason: str) -> dict:
    return {
        "status": "unavailable",
        "reason": reason,
        "modelVersion": MODEL_VERSION,
        "stage": STAGE,
        "condition": "lane1_not_first",
        "tickets": [],
    }


def attach_waiting(prediction: dict) -> None:
    prediction["flyPrediction"] = waiting_prediction()


def _finite_float(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FlyTicketsUnavailable(f"{field}_missing") from exc
    if not math.isfinite(number):
        raise FlyTicketsUnavailable(f"{field}_invalid")
    return number


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise FlyTicketsUnavailable(f"{field}_missing") from exc
    if number <= 0:
        raise FlyTicketsUnavailable(f"{field}_invalid")
    return number


@lru_cache(maxsize=4)
def _load_artifacts(artifact_directory: str) -> tuple[dict, dict, tuple[Any, ...]]:
    import lightgbm as lgb

    root = Path(artifact_directory)
    metadata = json.loads((root / "metadata.json").read_text(encoding="utf-8"))
    history = json.loads((root / "history.json").read_text(encoding="utf-8"))
    if metadata.get("modelVersion") != MODEL_VERSION:
        raise ValueError("tokoname_fly_v4_model_version_invalid")
    models = tuple(
        lgb.Booster(model_str=(root / filename).read_text(encoding="utf-8"))
        for filename in ("head.txt", "inside_position.txt", "pair.txt")
    )
    return metadata, history, models


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average = (index + 1 + end) / 2.0
        for position in range(index, end):
            ranks[indexed[position][0]] = average
        index = end
    return ranks


def _meeting_prior(racer: dict) -> tuple[float, int]:
    finishes = []
    for run in racer.get("season_runs") or []:
        match = re.search(r"[1-6]", str(run.get("finish") or ""))
        if match:
            finishes.append(int(match.group(0)))
    if not finishes:
        return 0.52, 0
    history = min(len(finishes), 80)
    return sum((7 - rank) / 6 for rank in finishes) / history, history


def _race_matrix(
    document: dict,
    race: dict,
    direct: dict,
    exhibition: dict,
    history: dict,
) -> tuple[Any, Any, list[int]]:
    import numpy as np

    racers = {
        int(item.get("lane") or 0): item
        for item in race.get("racers") or []
        if int(item.get("lane") or 0) in LANES
    }
    direct_racers = {
        int(item.get("lane") or 0): item
        for item in (direct.get("data") or {}).get("racers") or []
        if int(item.get("lane") or 0) in LANES
    }
    exhibition_rows = {
        int(item.get("lane") or 0): item
        for item in (exhibition.get("data") or {}).get("entries") or []
        if int(item.get("lane") or 0) in LANES
    }
    if set(racers) != set(LANES) or set(direct_racers) != set(LANES):
        raise FlyTicketsUnavailable("six_racers_required")
    if set(exhibition_rows) != set(LANES):
        raise FlyTicketsUnavailable("six_exhibition_rows_required")

    exhibition_times = [
        _finite_float(exhibition_rows[lane].get("exhibition_time"), f"lane_{lane}_exhibition")
        for lane in LANES
    ]
    exhibition_mean = sum(exhibition_times) / len(exhibition_times)
    exhibition_ranks = _average_ranks(exhibition_times)
    target_year = str(document.get("date") or "")[:4]
    if not re.fullmatch(r"\d{4}", target_year):
        raise FlyTicketsUnavailable("target_date_invalid")

    player_state = history.get("player") or {}
    motor_state = history.get("motor") or {}
    fallback_lanes = []
    matrix = []
    player_ids = []
    for lane in LANES:
        racer = racers[lane]
        player_id = _positive_int(
            direct_racers[lane].get("player_id") or racer.get("player_id"),
            f"lane_{lane}_player_id",
        )
        player_ids.append(player_id)
        player = player_state.get(str(player_id))
        if player is None:
            player = {"prior": 0.52, "history": 0, "st_prior": 0.18}
            fallback_lanes.append(lane)
        motor_no = _positive_int(
            racer.get("motor_no") or racer.get("motor"),
            f"lane_{lane}_motor",
        )
        motor = motor_state.get(f"{target_year}_{motor_no}") or {
            "prior": 0.52,
            "history": 0,
        }
        meet_prior, meet_history = _meeting_prior(racer)
        course = _positive_int(
            racer.get("actual_course")
            or racer.get("entry_course")
            or exhibition_rows[lane].get("exhibition_course")
            or lane,
            f"lane_{lane}_course",
        )
        if course not in LANES:
            raise FlyTicketsUnavailable(f"lane_{lane}_course_invalid")
        matrix.append(
            [
                float(player["prior"]),
                float(motor["prior"]),
                float(meet_prior),
                float(player["st_prior"]),
                exhibition_times[lane - 1],
                exhibition_times[lane - 1] - exhibition_mean,
                exhibition_ranks[lane - 1],
                float(course),
                float(lane),
                float(player["history"]),
                float(motor["history"]),
                float(meet_history),
            ]
        )

    lane1_inside = (history.get("inside") or {}).get(str(player_ids[0])) or {
        "two": 0.14,
        "three": 0.14,
        "out": 0.14,
    }
    inside = np.asarray(
        [lane1_inside["two"], lane1_inside["three"], lane1_inside["out"]],
        dtype="float32",
    )
    return np.asarray(matrix, dtype="float32"), inside, fallback_lanes


def _candidate_matrix(features: Any, inside: Any, options: Any) -> Any:
    import numpy as np

    race_vector = np.r_[features.flatten(), inside]
    rows = []
    for a, b, c in options:
        rows.append(
            np.r_[
                features[[a - 1, b - 1, c - 1], :].flatten(),
                features[a - 1, 4] - features[b - 1, 4],
                features[a - 1, 3] - features[c - 1, 3],
                race_vector[0:12],
                inside,
                features[b - 1] - features[a - 1],
                features[c - 1] - features[a - 1],
                features[c - 1] - features[b - 1],
                features[a - 1, 4] - features[0, 4],
                features[b - 1, 4] - features[0, 4],
                features[c - 1, 4] - features[0, 4],
                features[a - 1, 3] - features[0, 3],
                features[b - 1, 3] - features[0, 3],
                features[c - 1, 3] - features[0, 3],
                int(b == 1),
                int(c == 1),
                int(abs(b - a) == 1),
                int(abs(c - a) == 1),
                int(abs(c - b) == 1),
                int((a, b) in ((2, 3), (3, 4), (4, 5), (5, 6))),
                int((a, b, c) in ((2, 1, 3), (3, 4, 5), (3, 1, 4), (4, 5, 6))),
            ]
        )
    return np.asarray(rows, dtype="float32")


def predict(
    document: dict,
    race: dict,
    direct: dict,
    exhibition: dict,
    model_dir: Path,
) -> dict:
    import numpy as np

    artifact_root = model_dir / ARTIFACT_DIRECTORY
    metadata, history, models = _load_artifacts(str(artifact_root.resolve()))
    features, inside, fallback_lanes = _race_matrix(
        document,
        race,
        direct,
        exhibition,
        history,
    )
    options = np.asarray(metadata["options"], dtype=int)
    head_features = np.asarray(
        [
            np.r_[
                features[index],
                features[0],
                features[index, 4] - features[0, 4],
                features[index, 3] - features[0, 3],
                inside,
            ]
            for index in range(1, 6)
        ],
        dtype="float32",
    )
    race_vector = np.r_[features.flatten(), inside].astype("float32")
    candidates = _candidate_matrix(features, inside, options)

    head_model, position_model, pair_model = models
    head = np.asarray(head_model.predict(head_features), dtype=float)
    head /= head.sum()
    position = np.asarray(position_model.predict(race_vector.reshape(1, -1))[0], dtype=float)
    pair = np.asarray(pair_model.predict(candidates), dtype=float)
    conditional = np.zeros_like(pair)
    for head_index in range(5):
        indices = np.where(options[:, 0] == head_index + 2)[0]
        categories = np.where(
            options[indices, 1] == 1,
            0,
            np.where(options[indices, 2] == 1, 1, 2),
        )
        linked = pair[indices] * position[categories]
        total = linked.sum()
        if total <= 0:
            raise FlyTicketsUnavailable("conditional_probability_zero")
        conditional[indices] = head[head_index] * linked / total

    top = np.argsort(-conditional)[:10]
    tickets = [
        {
            "combo": "-".join(str(int(lane)) for lane in options[index]),
            "role": "飛び",
            "probability": round(float(conditional[index]) * 100.0, 2),
            "rank": rank,
        }
        for rank, index in enumerate(top, 1)
    ]
    return {
        "status": "complete",
        "reason": None,
        "modelVersion": MODEL_VERSION,
        "stage": STAGE,
        "condition": "lane1_not_first",
        "tickets": tickets,
        "candidateCount": int(len(options)),
        "normalizationProbabilityTotal": round(float(conditional.sum()) * 100.0, 6),
        "trainedThrough": metadata.get("trainedThrough"),
        "historyThrough": metadata.get("historyThrough"),
        "historyFallbackLanes": fallback_lanes,
        "validationReference": {
            "validation2024": metadata.get("validation"),
            "test2025Plus": metadata.get("test"),
            "exploratory": True,
            "futureAccuracyGuaranteed": False,
        },
        "inputAudit": {
            "oddsUsed": False,
            "resultUsed": False,
            "raceActualStartUsed": False,
            "originalExhibitionUsed": False,
            "normalExhibitionUsed": True,
        },
    }


def validate_prediction(value: dict, *, allow_waiting: bool = True) -> None:
    if value.get("modelVersion") != MODEL_VERSION:
        raise ValueError("tokoname_fly_v4_model_invalid")
    status = value.get("status")
    if status == "waiting" and allow_waiting:
        if value.get("tickets") != []:
            raise ValueError("tokoname_fly_v4_waiting_tickets_must_be_empty")
        return
    if status != "complete":
        raise ValueError("tokoname_fly_v4_not_complete")
    tickets = value.get("tickets")
    if not isinstance(tickets, list) or len(tickets) != 10:
        raise ValueError("tokoname_fly_v4_tickets_must_be_10")
    combos = [str(ticket.get("combo") or "") for ticket in tickets]
    if len(set(combos)) != 10:
        raise ValueError("tokoname_fly_v4_tickets_must_be_unique")
    if any(combo.startswith("1-") for combo in combos):
        raise ValueError("tokoname_fly_v4_lane1_head_forbidden")
    audit = value.get("inputAudit") or {}
    if any(
        audit.get(key) is not False
        for key in (
            "oddsUsed",
            "resultUsed",
            "raceActualStartUsed",
            "originalExhibitionUsed",
        )
    ):
        raise ValueError("tokoname_fly_v4_prohibited_input_used")
