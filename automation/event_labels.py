"""Preserve homepage meeting badges across the official-card publication path."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path


def normalize(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def title_grade(title: str) -> str:
    value = normalize(title).upper()
    # Official race cards omit the grade from some G3 meeting titles.
    if re.search(r"マスターズリーグ", value):
        return "G3"
    for token, grade in (("GIII", "G3"), ("GII", "G2"), ("GI", "G1"),
                         ("SG", "SG"), ("G3", "G3"), ("G2", "G2"), ("G1", "G1")):
        if re.search(rf"(?<![A-Z0-9]){token}(?![A-Z0-9])", value):
            return grade
    return ""


def historical_grade(payload: dict, data_root: Path) -> str:
    slug = str(payload.get("venueId") or payload.get("venue") or "")
    date_value = str(payload.get("date") or "")
    day = int(payload.get("eventDay") or 0)
    title = normalize(payload.get("officialEventTitle"))
    if not slug or not day or not title:
        return ""
    try:
        current = date.fromisoformat(date_value)
    except ValueError:
        return ""
    for offset in range(1, min(6, day - 1) + 1):
        path = data_root / "venues" / slug / f"{(current - timedelta(days=offset)):%Y%m%d}.json"
        if not path.is_file():
            continue
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(previous, dict) or int(previous.get("eventDay") or 0) != day - offset:
            continue
        earlier_title = normalize(previous.get("officialEventTitle"))
        if earlier_title and earlier_title != title:
            continue
        label = normalize(previous.get("eventLabel"))
        match = re.search(r"(?:^|[｜|])\s*(SG|G[1-3])(?:[｜|]|$)", label)
        if match:
            return match.group(1)
    return ""


def event_metadata(payload: dict, data_root: Path) -> tuple[str, str]:
    title = normalize(payload.get("officialEventTitle"))
    existing = normalize(payload.get("eventLabel"))
    grade = title_grade(title) or historical_grade(payload, data_root)
    if not grade:
        match = re.search(r"(?:^|[｜|])\s*(SG|G[1-3])(?:[｜|]|$)", existing)
        grade = match.group(1) if match else ""
    women = bool(re.search(r"女子|レディース|ヴィーナス|WOMEN|レディス", title, re.I)) or "♥" in existing
    rookie = bool(re.search(r"ルーキー|ヤング|新人", title)) or "🔰" in existing
    event_type = str(payload.get("eventType") or "")
    if not event_type or event_type == "unknown":
        event_type = "women" if women else "rookie" if rookie else "unknown"
    return "｜".join(filter(None, [grade, "🔰" if rookie else "", "♥️" if women else ""])), event_type
