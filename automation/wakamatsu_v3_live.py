"""Safety-gated LIVE adapter for the frozen Wakamatsu v3 lane-1 tide model.

This module is intentionally not wired to the public JSON/site yet.  It never
falls back to a cached tide payload or to the legacy fly probability.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from io import StringIO
import math
import re
from typing import Any, Callable
import urllib.request
from zoneinfo import ZoneInfo

import pandas as pd

try:
    from .wakamatsu_v3_frozen_inference import predict_live as frozen_predict_live
except ImportError:  # pragma: no cover - direct script execution
    from wakamatsu_v3_frozen_inference import predict_live as frozen_predict_live


JST = ZoneInfo("Asia/Tokyo")
SOURCE_URL = "https://tide.chowari.jp/40/401005/20852/"
BOUNDARY_MINUTES = 5
VALID_PHASES = {"rising", "falling"}


def _iso_date(value: str) -> str:
    return date.fromisoformat(value).isoformat()


def _request_text(url: str, timeout: int = 30) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 Chrome/126 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.geturl(), response.read().decode("utf-8", errors="replace")


def page_date(html: str) -> str | None:
    """Return the date asserted by the page itself, never a caller date."""
    candidates: set[str] = set()
    for tag in re.findall(r"<option\b[^>]*>", html, flags=re.I):
        if not re.search(r"\bselected(?:\s*=|\s|>)", tag, flags=re.I):
            continue
        match = re.search(r"\bvalue=[\"'](\d{8})[\"']", tag, flags=re.I)
        if match:
            value = match.group(1)
            candidates.add(f"{value[:4]}-{value[4:6]}-{value[6:8]}")

    for match in re.finditer(
        r"(?:本日|今日)?\s*[（(]?(\d{4})年(\d{1,2})月(\d{1,2})日",
        html,
    ):
        candidates.add(
            f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        )
    return next(iter(candidates)) if len(candidates) == 1 else None


def _split_values(value: object) -> list[str]:
    return [part for part in re.split(r"\s+", str(value).strip()) if part]


def page_events(html: str, source_date: str) -> list[dict[str, Any]]:
    """Extract dated extrema explicitly displayed for today and future days."""
    tables = pd.read_html(StringIO(html))
    current_events: list[dict[str, Any]] = []
    for table in tables:
        if table.shape[0] < 3 or table.shape[1] < 2:
            continue
        times = sum(
            (_split_values(table.iloc[0, col]) for col in range(1, min(5, table.shape[1]))),
            [],
        )
        types = sum(
            (_split_values(table.iloc[1, col]) for col in range(1, min(5, table.shape[1]))),
            [],
        )
        levels = sum(
            (_split_values(table.iloc[2, col]) for col in range(1, min(5, table.shape[1]))),
            [],
        )
        events = []
        for kind, time_text, level_text in zip(types, times, levels):
            normalized_kind = (
                "high" if "満潮" in str(kind) else "low" if "干潮" in str(kind) else None
            )
            level_match = re.search(r"-?\d+", str(level_text))
            if (
                normalized_kind
                and re.fullmatch(r"\d{1,2}:\d{2}", str(time_text))
                and level_match
            ):
                events.append(
                    {
                        "date": source_date,
                        "type": normalized_kind,
                        "time": str(time_text),
                        "level": int(level_match.group(0)),
                    }
                )
        if len(events) >= 2 and {event["type"] for event in events} == {"high", "low"}:
            current_events = events
            break
    if not current_events:
        return []

    source = date.fromisoformat(source_date)
    future_events: list[dict[str, Any]] = []
    for table in tables:
        if table.shape[1] < 5:
            continue
        for _, row in table.iterrows():
            match = re.search(r"(\d{1,2})月(\d{1,2})日", str(row.iloc[0]))
            if not match:
                continue
            month, day = int(match.group(1)), int(match.group(2))
            year = source.year + (1 if month < source.month else 0)
            try:
                event_date = date(year, month, day)
            except ValueError:
                continue
            if event_date <= source:
                continue
            for kind, time_cell, level_cell in (
                ("high", row.iloc[1], row.iloc[2]),
                ("low", row.iloc[3], row.iloc[4]),
            ):
                times = _split_values(time_cell)
                levels = _split_values(level_cell)
                for time_text, level_text in zip(times, levels):
                    level_match = re.search(r"-?\d+", str(level_text))
                    if re.fullmatch(r"\d{1,2}:\d{2}", time_text) and level_match:
                        future_events.append(
                            {
                                "date": event_date.isoformat(),
                                "type": kind,
                                "time": time_text,
                                "level": int(level_match.group(0)),
                            }
                        )
    return sorted(
        current_events + future_events,
        key=lambda event: (event["date"], event["time"]),
    )


def fetch_validated_tide(
    target_date: str,
    *,
    now: datetime | None = None,
    opener: Callable[[str], tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """Fetch and validate fresh Chowari data without reading any cache."""
    target_date = _iso_date(target_date)
    fetched_at = (now or datetime.now(JST)).astimezone(JST)
    if fetched_at.date().isoformat() != target_date:
        return {
            "status": "unavailable",
            "reason": "fetch_date_mismatch",
            "targetDate": target_date,
            "fetchedAt": fetched_at.isoformat(),
            "sourceUrl": SOURCE_URL,
            "fresh": False,
        }
    request = opener or _request_text
    try:
        resolved_url, html = request(SOURCE_URL)
    except Exception as exc:
        return {
            "status": "unavailable",
            "reason": f"fetch_failed:{type(exc).__name__}",
            "targetDate": target_date,
            "sourceUrl": SOURCE_URL,
            "fresh": False,
        }

    actual_date = page_date(html)
    if actual_date is None:
        return {
            "status": "unavailable",
            "reason": "page_date_unverified",
            "targetDate": target_date,
            "sourceUrl": resolved_url,
            "fresh": False,
        }
    if actual_date != target_date:
        return {
            "status": "unavailable",
            "reason": "page_date_mismatch",
            "targetDate": target_date,
            "sourceDate": actual_date,
            "sourceUrl": resolved_url,
            "fresh": False,
        }

    events = page_events(html, actual_date)
    if not events:
        return {
            "status": "unavailable",
            "reason": "tide_extrema_missing",
            "targetDate": target_date,
            "sourceDate": actual_date,
            "sourceUrl": resolved_url,
            "fresh": False,
        }
    return {
        "status": "ok",
        "reason": "validated_fresh_fetch",
        "targetDate": target_date,
        "sourceDate": actual_date,
        "fetchedAt": fetched_at.isoformat(),
        "sourceUrl": resolved_url,
        "fresh": True,
        "events": events,
    }


def validate_tide_payload(payload: dict[str, Any], target_date: str) -> str | None:
    """Return an unavailability reason, or None for a usable fresh payload."""
    target_date = _iso_date(target_date)
    if payload.get("status") != "ok":
        return str(payload.get("reason") or "fetch_not_successful")
    if payload.get("fresh") is not True:
        return "tide_freshness_unverified"
    if payload.get("sourceDate") != target_date or payload.get("targetDate") != target_date:
        return "tide_date_mismatch"
    fetched_at_text = payload.get("fetchedAt")
    if not fetched_at_text:
        return "tide_freshness_unverified"
    try:
        fetched_at = datetime.fromisoformat(str(fetched_at_text))
    except ValueError:
        return "tide_fetched_at_invalid"
    if fetched_at.tzinfo is None:
        return "tide_fetched_at_timezone_missing"
    if fetched_at.astimezone(JST).date().isoformat() != target_date:
        return "tide_stale_for_target_date"
    return None


def _minute(value: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value or ""))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def tide_input_for_race(
    payload: dict[str, Any],
    target_date: str,
    deadline: str,
    *,
    tide_phase: str | None = None,
    independently_validated_phase: str | None = None,
) -> dict[str, Any]:
    reason = validate_tide_payload(payload, target_date)
    if reason:
        return {"status": "unavailable", "reason": reason}
    race_minute = _minute(deadline)
    if race_minute is None:
        return {"status": "unavailable", "reason": "deadline_invalid"}
    events = []
    target_day = date.fromisoformat(target_date)
    for event in payload.get("events") or []:
        if not isinstance(event, dict):
            continue
        minute = _minute(event.get("time"))
        kind = event.get("type")
        try:
            event_day = date.fromisoformat(str(event.get("date")))
        except (TypeError, ValueError):
            continue
        day_offset = (event_day - target_day).days
        if day_offset not in {0, 1} or minute is None or kind not in {"high", "low"}:
            continue
        events.append({"minute": day_offset * 1440 + minute, "type": kind})
    events.sort(key=lambda event: event["minute"])

    highs = [event for event in events if event["type"] == "high"]
    lows = [event for event in events if event["type"] == "low"]
    previous = next((event for event in reversed(events) if event["minute"] <= race_minute), None)
    following = next((event for event in events if event["minute"] > race_minute), None)
    each_kind_bracketed = all(
        any(event["minute"] <= race_minute for event in group)
        and any(event["minute"] > race_minute for event in group)
        for group in (highs, lows)
    )
    if not highs or not lows or previous is None or following is None or not each_kind_bracketed:
        return {"status": "unavailable", "reason": "required_tide_extrema_missing"}

    nearest = min(abs(race_minute - event["minute"]) for event in events)
    if previous["type"] == "low" and following["type"] == "high":
        expected_phase = "rising"
    elif previous["type"] == "high" and following["type"] == "low":
        expected_phase = "falling"
    else:
        return {"status": "unavailable", "reason": "tide_phase_unresolved"}
    boundary = nearest <= BOUNDARY_MINUTES
    resolved_phase = expected_phase if tide_phase not in VALID_PHASES else tide_phase
    if (
        independently_validated_phase in VALID_PHASES
        and independently_validated_phase != expected_phase
    ):
        return {"status": "unavailable", "reason": "tide_phase_mismatch"}
    if resolved_phase != expected_phase:
        return {"status": "unavailable", "reason": "tide_phase_mismatch"}

    return {
        "status": "ok",
        "reason": "validated",
        "boundary": boundary,
        "tide_phase": resolved_phase,
        "minutes_from_high_tide": min(abs(race_minute - event["minute"]) for event in highs),
        "minutes_from_low_tide": min(abs(race_minute - event["minute"]) for event in lows),
    }


def normalized_live_row(row: dict[str, Any]) -> dict[str, Any]:
    """Copy the row and map an invalid local win rate to frozen-model missing."""
    normalized = deepcopy(row)
    try:
        value = float(normalized.get("boat1_local_win_rate", ""))
    except (TypeError, ValueError):
        value = math.nan
    if not math.isfinite(value) or not 0 < value <= 10:
        normalized["boat1_local_win_rate"] = ""
    return normalized


def predict_v3(
    row: dict[str, Any],
    tide_payload: dict[str, Any],
    *,
    target_date: str,
    deadline: str,
    input_stage: str,
    independently_validated_phase: str | None = None,
) -> dict[str, Any]:
    """Return one gated v3 probability or an explicit unavailability result."""
    if input_stage != "live":
        return {"status": "unavailable", "reason": "live_input_required", "lane1FlyProbability": None}
    tide = tide_input_for_race(
        tide_payload,
        target_date,
        deadline,
        tide_phase=(row.get("tide_phase") or row.get("pred_tide_phase")),
        independently_validated_phase=independently_validated_phase,
    )
    if tide.get("status") != "ok":
        return {**tide, "lane1FlyProbability": None}
    try:
        prediction = frozen_predict_live(normalized_live_row(row), tide, strict=True)
    except (TypeError, ValueError) as exc:
        return {
            "status": "unavailable",
            "reason": str(exc),
            "lane1FlyProbability": None,
        }
    return {
        "status": "ok",
        "reason": "validated_live_v3",
        "lane1FlyProbability": prediction["v3_lane1_tide_pct"],
        "model": prediction["model"],
        "source": "live",
        "tide": tide,
        "comparisonLog": {
            "legacyFlyProbability": prediction["existing_fly_pct"],
            "v3FlyProbability": prediction["v3_lane1_tide_pct"],
        },
    }
