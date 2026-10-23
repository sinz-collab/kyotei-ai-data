from __future__ import annotations

import asyncio
import json
import signal
from datetime import timedelta

from live_common import (
    configure_logging,
    is_fetch_window,
    load_config,
    next_start,
    now_local,
    process_lock,
    resolve_root,
)
from live_fetch_once import run_once


async def daemon() -> int:
    config = load_config()
    logger = configure_logging(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                loop.add_signal_handler(getattr(signal, name), stop.set)
            except NotImplementedError:
                pass
    lock_path = resolve_root(config, "lock_path")
    with process_lock(lock_path) as acquired:
        if not acquired:
            logger.error("another daemon is already running", extra={"event": "daemon_lock_failed"})
            return 1
        logger.info("live monitor started", extra={"event": "daemon_started"})
        previous_state = ""
        interval = timedelta(minutes=config["interval_minutes"])
        configured_priority_venues = config.get("priority_venues")
        if configured_priority_venues is None:
            configured_priority_venues = [config.get("priority_venue")]
        priority_venues = {
            str(venue).strip()
            for venue in configured_priority_venues
            if str(venue or "").strip()
        }
        priority_interval = timedelta(
            minutes=int(config.get("priority_interval_minutes") or config["interval_minutes"])
        )
        last_started = None
        last_priority_started = None
        while not stop.is_set():
            now = now_local(config)
            if not is_fetch_window(now, config):
                state = "outside_window"
                if state != previous_state:
                    logger.info(
                        f"outside fetch window; next start {next_start(now, config).isoformat()}",
                        extra={"event": state},
                    )
                    previous_state = state
                wait_seconds = min(60.0, max(1.0, (next_start(now, config) - now).total_seconds()))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    pass
                continue
            full_due = last_started is None or now - last_started >= interval
            priority_due = bool(priority_venues) and (
                last_priority_started is None
                or now - last_priority_started >= priority_interval
            )
            if not full_due and not priority_due:
                waits = [(interval - (now - last_started)).total_seconds()]
                if priority_venues and last_priority_started is not None:
                    waits.append(
                        (priority_interval - (now - last_priority_started)).total_seconds()
                    )
                wait_seconds = max(1.0, min(waits))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    pass
                continue
            venue_filter = None
            if full_due:
                last_started = now
                last_priority_started = now
            else:
                venue_filter = priority_venues
                last_priority_started = now
            try:
                result = await run_once(config, now=now, venue_filter=venue_filter)
                state = result["status"]
                if state != previous_state or state == "completed":
                    logger.info(
                        json.dumps(result, ensure_ascii=False, default=str),
                        extra={"event": state, "changed": result.get("changed")},
                    )
                previous_state = state
            except Exception as exc:
                logger.exception(str(exc), extra={"event": "cycle_failed"})
        logger.info("live monitor stopped", extra={"event": "daemon_stopped"})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(daemon()))

