from __future__ import annotations

from typing import Any


REQUIRED = {
    "date",
    "venue",
    "race_no",
    "deadline",
    "fetched_at",
    "source",
    "status",
    "complete",
    "content_hash",
    "error",
    "data",
}


def _identity_map(racers: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(row.get("lane") or 0): row for row in racers}


def validate_live_data(
    document: dict[str, Any],
    expected: dict[str, Any],
    item_type: str,
) -> list[str]:
    errors = []
    missing = sorted(REQUIRED - document.keys())
    if missing:
        errors.append(f"missing required keys: {missing}")
    for key in ("date", "venue", "race_no", "deadline"):
        if str(document.get(key)) != str(expected.get(key)):
            errors.append(f"{key} mismatch")
    if document.get("status") not in {
        "pending",
        "partial",
        "complete",
        "fetch_error",
        "parse_error",
        "cancelled",
        "withdrawal",
    }:
        errors.append("invalid status")
    data = document.get("data") or {}
    entries = data.get("entries") or data.get("racers") or []
    if entries:
        lanes = [int(row.get("lane") or 0) for row in entries]
        if any(lane not in range(1, 7) for lane in lanes):
            errors.append("invalid lane")
        if len(lanes) != len(set(lanes)):
            errors.append("duplicate lane")
        morning = _identity_map(expected.get("racers") or [])
        for row in entries:
            lane = int(row.get("lane") or 0)
            source_racer = morning.get(lane, {})
            expected_id = source_racer.get("player_id") or source_racer.get("registration_number")
            actual_id = row.get("player_id")
            if expected_id and actual_id and str(expected_id) != str(actual_id):
                errors.append(f"player id mismatch lane={lane}")
            expected_name = "".join(str(source_racer.get("name") or "").split())
            actual_name = "".join(str(row.get("name") or "").split())
            if not expected_id and expected_name and actual_name and expected_name != actual_name:
                errors.append(f"player name mismatch lane={lane}")
    if item_type == "odds":
        odds = data.get("odds") or {}
        if len(odds) > 120:
            errors.append("odds count exceeds 120")
        if len(odds) != len(set(odds)):
            errors.append("duplicate odds key")
        for key in odds:
            parts = key.split("-")
            if len(parts) != 3 or len(set(parts)) != 3 or any(part not in "123456" for part in parts):
                errors.append(f"invalid odds key: {key}")
        if document.get("complete") is True and len(odds) != 120:
            errors.append("complete odds must contain 120 combinations")
    if item_type == "result":
        order = [str(value) for value in (data.get("order") or [])]
        if any(value not in "123456" for value in order) or len(order) != len(set(order)):
            errors.append("invalid result order")
        if document.get("complete") is True and len(order) != 3:
            errors.append("complete result must contain three finishers")
    if document.get("complete") is True and document.get("status") != "complete":
        errors.append("complete/status mismatch")
    return errors
