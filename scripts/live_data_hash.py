from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any


HASH_EXCLUDED_KEYS = {
    "fetched_at",
    "content_hash",
    "retry_count",
    "http_status",
    "duration_ms",
    "temporary",
}


def _normalize_number(value: float) -> int | float | str:
    try:
        decimal = Decimal(str(value)).normalize()
    except InvalidOperation:
        return str(value)
    if decimal == decimal.to_integral():
        return int(decimal)
    return float(decimal)


def normalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): normalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key not in HASH_EXCLUDED_KEYS
        }
    if isinstance(value, list):
        normalized = [normalize_for_hash(item) for item in value]
        if all(isinstance(item, dict) and "lane" in item for item in normalized):
            return sorted(normalized, key=lambda item: int(item["lane"]))
        if all(isinstance(item, dict) and "combination" in item for item in normalized):
            return sorted(normalized, key=lambda item: item["combination"])
        return normalized
    if value == "":
        return None
    if isinstance(value, float):
        return _normalize_number(value)
    return value


def content_hash(data: dict[str, Any]) -> str:
    normalized = normalize_for_hash(data)
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

