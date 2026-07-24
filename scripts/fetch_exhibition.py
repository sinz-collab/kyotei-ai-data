from __future__ import annotations

from typing import Any

from fetch_direct_info import apollo_state, dereference, race_object, race_racer_map


def _valid_exhibition_time(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not 5.0 <= parsed < 10.0:
        return None
    return round(parsed, 2)


def normalize_exhibition_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized = []
    for source in rows:
        row = dict(source)
        row["exhibition_time"] = _valid_exhibition_time(row.get("exhibition_time"))
        normalized.append(row)

    valid_times = [
        row["exhibition_time"]
        for row in normalized
        if row["exhibition_time"] is not None
    ]
    fastest = min(valid_times) if valid_times else None
    for row in normalized:
        value = row["exhibition_time"]
        if value is None:
            row["exhibition_rank"] = None
            row["exhibition_gap"] = None
            continue
        row["exhibition_rank"] = 1 + sum(other < value for other in valid_times)
        row["exhibition_gap"] = round(value - fastest, 2)
    return normalized


def parse_exhibition(html_text: str, race_no: int) -> dict[str, Any]:
    state = apollo_state(html_text)
    race = race_object(state, race_no)
    identities = race_racer_map(state, race)
    before = dereference(state, race.get("beforeInfo"))
    rows = []
    for reference in before.get("racers") or []:
        item = dereference(state, reference)
        lane = int(item.get("boatNumber") or 0)
        if lane not in range(1, 7):
            continue
        identity = identities.get(lane, {})
        rows.append(
            {
                "lane": lane,
                "player_id": identity.get("regN"),
                "name": identity.get("name"),
                "exhibition_course": item.get("startSinnyu") or lane,
                "exhibition_time": item.get("tenjiTime"),
                "exhibition_rank": item.get("tenjiRank"),
                "start_time": item.get("startTenjiTime"),
                "start_rank": item.get("startTenjiRank"),
                "start_raw": item.get("startTenjiTime"),
                "tilt": item.get("tilt"),
            }
        )
    rows.sort(key=lambda row: row["lane"])
    rows = normalize_exhibition_entries(rows)
    published = any(
        row.get("exhibition_time") is not None or row.get("start_time") is not None
        for row in rows
    )
    complete = len(rows) == 6 and all(
        row.get("exhibition_time") is not None and row.get("start_time") is not None
        for row in rows
    )
    start_order = sorted(
        (
            {"lane": row["lane"], "start_time": row["start_time"]}
            for row in rows
            if row.get("start_time") is not None
        ),
        key=lambda row: float(row["start_time"]),
    )
    return {
        "entries": rows,
        "start_order": [row["lane"] for row in start_order],
        "slit_source": [
            {
                "lane": row["lane"],
                "course": row["exhibition_course"],
                "start_time": row["start_time"],
                "start_raw": row["start_raw"],
            }
            for row in rows
        ],
        "_published": published,
        "_complete": complete,
    }
