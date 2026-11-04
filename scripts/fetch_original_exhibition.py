from __future__ import annotations

import re
from html import unescape
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


def _kiryu_number(value: str) -> float | None:
    text = re.sub(r"<[^>]+>", "", unescape(value)).strip()
    if text in {"", "-", "—"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def parse_kiryu_original_exhibition(html_text: str) -> dict[str, Any]:
    """Parse Kiryu's official half-lap/turn/straight measurements.

    Kiryu publishes a half-lap value in the first original-exhibition column,
    not the one-lap value exposed by the generic BOATERS schema.
    """
    rows = []
    for block in re.findall(r"<tr\b[^>]*>.*?</tr>", html_text, re.I | re.S):
        lane_match = re.search(
            r'<td\b[^>]*class=["\'][^"\']*\bcol1\b[^"\']*["\'][^>]*>(.*?)</td>',
            block,
            re.I | re.S,
        )
        if not lane_match:
            continue
        lane_text = re.sub(r"<[^>]+>", "", unescape(lane_match.group(1))).strip()
        if not re.fullmatch(r"[1-6]", lane_text):
            continue

        values: dict[str, float | None] = {}
        for class_name, key in (
            ("col5", "half_lap_time"),
            ("col6", "turn_time"),
            ("col7", "straight_time"),
        ):
            match = re.search(
                rf'<td\b[^>]*class=["\'][^"\']*\b{class_name}\b[^"\']*["\'][^>]*>(.*?)</td>',
                block,
                re.I | re.S,
            )
            values[key] = _kiryu_number(match.group(1)) if match else None
        rows.append({"lane": int(lane_text), **values})

    rows.sort(key=lambda row: row["lane"])
    lanes = {row["lane"] for row in rows}
    published = any(
        row.get(key) is not None
        for row in rows
        for key in ("half_lap_time", "turn_time", "straight_time")
    )
    complete = lanes == set(range(1, 7)) and all(
        row.get("half_lap_time") is not None
        and row.get("turn_time") is not None
        and row.get("straight_time") is not None
        for row in rows
    )
    return {"entries": rows, "_published": published, "_complete": complete}


def merge_kiryu_original_exhibition(
    generic: dict[str, Any],
    official: dict[str, Any],
) -> dict[str, Any]:
    """Keep BOATERS identities while using Kiryu's official measured values."""
    generic_by_lane = {
        int(row.get("lane") or 0): row
        for row in generic.get("entries") or []
        if int(row.get("lane") or 0) in range(1, 7)
    }
    merged = []
    for measured in official.get("entries") or []:
        lane = int(measured.get("lane") or 0)
        if lane not in range(1, 7):
            continue
        row = dict(generic_by_lane.get(lane, {}))
        row.pop("lap_time", None)
        row["lane"] = lane
        for key in ("half_lap_time", "turn_time", "straight_time"):
            if measured.get(key) is not None:
                row[key] = measured[key]
            elif key not in row:
                row[key] = None
        merged.append(row)
    merged.sort(key=lambda row: row["lane"])
    published = any(
        row.get(key) is not None
        for row in merged
        for key in ("half_lap_time", "turn_time", "straight_time")
    )
    complete = {row["lane"] for row in merged} == set(range(1, 7)) and all(
        row.get("half_lap_time") is not None
        and row.get("turn_time") is not None
        and row.get("straight_time") is not None
        for row in merged
    )
    return {
        "entries": merged,
        "metric": "half_lap",
        "_published": published,
        "_complete": complete,
    }
