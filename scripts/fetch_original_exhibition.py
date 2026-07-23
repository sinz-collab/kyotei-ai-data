from __future__ import annotations

import re
from typing import Any

from fetch_direct_info import apollo_state, dereference, race_object, race_racer_map


def _sum_rows(body_text: str) -> dict[int, dict[str, Any]]:
    lines = [line.strip() for line in body_text.splitlines() if line.strip()]
    start = next((index for index, line in enumerate(lines) if "SUM" in line.upper()), -1)
    if start < 0:
        return {}
    rows: dict[int, dict[str, Any]] = {}
    index = start + 1
    while index + 6 < len(lines):
        if not re.fullmatch(r"[1-6]", lines[index]):
            index += 1
            continue
        lane = int(lines[index])
        rows[lane] = {
            "sum_lap": lines[index + 3],
            "sum_exhibition": lines[index + 4],
            "sum": lines[index + 5],
            "sum_difference": lines[index + 6],
        }
        index += 7
    return rows


def parse_original_exhibition(html_text: str, body_text: str, race_no: int) -> dict[str, Any]:
    state = apollo_state(html_text)
    race = race_object(state, race_no)
    identities = race_racer_map(state, race)
    sums = _sum_rows(body_text)
    rows = []
    for reference in race.get("originalTenjis") or []:
        item = dereference(state, reference)
        lane = int(item.get("boatNumber") or 0)
        if lane not in range(1, 7):
            continue
        identity = identities.get(lane, {})
        row = {
            "lane": lane,
            "player_id": identity.get("regN"),
            "name": identity.get("name"),
            "lap_time": item.get("isshuTime"),
            "turn_time": item.get("mawariashiTime"),
            "straight_time": item.get("chokusenTime"),
        }
        row.update(sums.get(lane, {}))
        rows.append(row)
    rows.sort(key=lambda row: row["lane"])
    published = bool(rows) or bool(sums)
    complete = len(rows) == 6 and all(
        row.get("lap_time") is not None
        and row.get("turn_time") is not None
        and row.get("straight_time") is not None
        for row in rows
    )
    return {"entries": rows, "_published": published, "_complete": complete}
