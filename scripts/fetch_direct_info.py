from __future__ import annotations

import html
import json
import re
from typing import Any


def parse_next_data(html_text: str) -> dict[str, Any]:
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html_text,
        re.S,
    )
    if not match:
        raise ValueError("HTML structure changed: __NEXT_DATA__ not found")
    return json.loads(html.unescape(match.group(1)))


def apollo_state(html_text: str) -> dict[str, Any]:
    return (
        parse_next_data(html_text)
        .get("props", {})
        .get("pageProps", {})
        .get("initialApolloState", {})
    )


def race_object(state: dict[str, Any], race_no: int) -> dict[str, Any]:
    for item in state.values():
        if isinstance(item, dict) and item.get("__typename") == "CrawledRace":
            if int(item.get("round") or 0) == race_no:
                return item
    raise ValueError(f"HTML structure changed: race {race_no} object not found")


def dereference(state: dict[str, Any], value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("__ref"):
        target = state.get(value["__ref"], {})
        return target if isinstance(target, dict) else {}
    return value if isinstance(value, dict) else {}


def race_racer_map(
    state: dict[str, Any],
    race: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    output = {}
    for reference in race.get("racers") or []:
        item = dereference(state, reference)
        lane = int(item.get("boatNumber") or 0)
        if lane in range(1, 7):
            output[lane] = item
    return output


def _keyword_flags(text: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", text)
    return {
        "stabilizer": True if "安定板" in normalized and "使用" in normalized else None,
        "lap_shortened": True if "周回短縮" in normalized else None,
        "entry_changed": True if "進入変更" in normalized else False,
        "withdrawals": re.findall(r"([1-6])号艇[^。]*(?:欠場|途中帰郷)", normalized),
        "other_changes": [
            label
            for label in ("欠場", "途中帰郷", "部品交換", "重量調整", "進入変更", "周回短縮", "安定板")
            if label in normalized
        ],
    }


def parse_direct_info(html_text: str, body_text: str, race_no: int) -> dict[str, Any]:
    state = apollo_state(html_text)
    race = race_object(state, race_no)
    identities = race_racer_map(state, race)
    before = dereference(state, race.get("beforeInfo"))
    racers = []
    for reference in before.get("racers") or []:
        item = dereference(state, reference)
        lane = int(item.get("boatNumber") or 0)
        if lane not in range(1, 7):
            continue
        identity = identities.get(lane, {})
        racers.append(
            {
                "lane": lane,
                "player_id": identity.get("regN"),
                "name": identity.get("name"),
                "weight": item.get("weight"),
                "weight_adjustment": item.get("weightAdjust"),
                "tilt": item.get("tilt"),
                "parts_exchange": item.get("partsExchange") or [],
                "withdrawn": bool(item.get("isAbsent") or item.get("absent")),
            }
        )
    flags = _keyword_flags(body_text)
    published = bool(before) and (bool(racers) or before.get("weather") is not None)
    complete = len(racers) == 6 and len({row["lane"] for row in racers}) == 6
    return {
        "weather": before.get("weather"),
        "air_temperature": before.get("weatherDegree"),
        "water_temperature": before.get("waterDegree"),
        "wind_direction": before.get("windDirection"),
        "wind_speed": before.get("windSpeed"),
        "wave_height": before.get("waveHeight"),
        "stabilizer": flags["stabilizer"],
        "lap_shortened": flags["lap_shortened"],
        "actual_entry": [
            row["lane"]
            for row in sorted(
                (
                    {
                        "lane": int(dereference(state, ref).get("boatNumber") or 0),
                        "course": int(
                            dereference(state, ref).get("startSinnyu")
                            or dereference(state, ref).get("boatNumber")
                            or 0
                        ),
                    }
                    for ref in before.get("racers") or []
                    if int(dereference(state, ref).get("boatNumber") or 0) in range(1, 7)
                ),
                key=lambda row: row["course"],
            )
        ],
        "entry_changed": flags["entry_changed"],
        "withdrawals": flags["withdrawals"],
        "racers": sorted(racers, key=lambda row: row["lane"]),
        "other_changes": flags["other_changes"],
        "_published": published,
        "_complete": complete,
    }
