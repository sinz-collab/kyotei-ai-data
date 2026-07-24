from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from live_common import atomic_write_json, is_fetch_window, load_json, resolve_root


def _request_json(url: str, config: dict[str, Any], logger: Any) -> dict[str, Any]:
    last_error: Exception | None = None
    timeout = config["connect_timeout_seconds"] + config["read_timeout_seconds"]
    for attempt in range(1, config["max_retries"] + 1):
        if not is_fetch_window(None, config):
            raise RuntimeError("fetch window closed; morning data retry not started")
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": config["source_user_agent"],
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"morning data HTTP status {response.status}")
                data = json.loads(response.read().decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("morning data response is not a JSON object")
                return data
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            last_error = exc
            logger.warning(
                str(exc),
                extra={
                    "event": "morning_sync_retry",
                    "source": url,
                    "attempt": attempt,
                },
            )
            if attempt >= config["max_retries"] or not is_fetch_window(None, config):
                break
            time.sleep((2 ** (attempt - 1)) + random.uniform(0.2, 1.0))
    raise RuntimeError(f"morning data download failed: {last_error}")


def _valid_races(payload: dict[str, Any], today: str) -> bool:
    races = payload.get("races")
    return (
        payload.get("date") == today
        and isinstance(races, list)
        and len(races) == 12
        and all(len(race.get("racers") or []) == 6 for race in races)
        and all(
            re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", str(race.get("deadline") or ""))
            for race in races
        )
    )


def _local_is_current(root: Path, today: str) -> bool:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = load_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if manifest.get("date") != today:
        return False
    for venue in manifest.get("venues") or []:
        if not venue.get("open"):
            continue
        data_path = str(venue.get("dataPath") or "")
        if (
            venue.get("date") != today
            or int(venue.get("entryCount") or 0) != 12
            or not data_path
        ):
            return False
        payload_path = root / data_path
        if not payload_path.is_file():
            return False
        try:
            if not _valid_races(load_json(payload_path), today):
                return False
        except (OSError, ValueError, json.JSONDecodeError):
            return False
    return True


def _manifest_updated_at(manifest: dict[str, Any]) -> datetime | None:
    value = str(manifest.get("updatedAt") or "")
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ensure_current_morning_data(
    config: dict[str, Any],
    today: str,
    logger: Any,
) -> Path:
    root = resolve_root(config, "morning_data_root")
    manifest_path = root / "manifest.json"
    local_is_current = _local_is_current(root, today)
    local_manifest = load_json(manifest_path) if local_is_current else None

    base_url = str(config["morning_data_base_url"]).rstrip("/")
    try:
        manifest = _request_json(f"{base_url}/manifest.json", config, logger)
    except RuntimeError:
        if not local_is_current:
            raise
        logger.warning(
            f"morning manifest refresh failed; retaining validated local data for {today}",
            extra={"event": "morning_sync_using_local"},
        )
        return manifest_path

    if manifest.get("date") != today:
        if local_is_current:
            logger.warning(
                (
                    "remote morning manifest is not current; retaining validated local data: "
                    f"expected={today} actual={manifest.get('date')}"
                ),
                extra={"event": "morning_sync_using_local"},
            )
            return manifest_path
        raise RuntimeError(
            f"remote morning manifest is not current: expected={today} actual={manifest.get('date')}"
        )

    if local_manifest is not None:
        local_updated = _manifest_updated_at(local_manifest)
        remote_updated = _manifest_updated_at(manifest)
        if manifest == local_manifest or (
            local_updated is not None
            and remote_updated is not None
            and remote_updated <= local_updated
        ):
            return manifest_path

    payloads: list[tuple[Path, dict[str, Any]]] = []
    for venue in manifest.get("venues") or []:
        if not venue.get("open"):
            continue
        data_path = str(venue.get("dataPath") or "")
        if (
            venue.get("date") != today
            or int(venue.get("entryCount") or 0) != 12
            or not data_path
        ):
            raise RuntimeError(f"invalid open venue in morning manifest: {venue.get('slug')}")
        payload = _request_json(f"{base_url}/{data_path}", config, logger)
        if not _valid_races(payload, today):
            raise RuntimeError(f"invalid morning venue payload: {venue.get('slug')}")
        payloads.append((root / data_path, payload))

    for path, payload in payloads:
        atomic_write_json(path, payload)
    atomic_write_json(manifest_path, manifest)
    logger.info(
        f"morning data synchronized for {today}",
        extra={"event": "morning_sync_complete"},
    )
    return manifest_path
