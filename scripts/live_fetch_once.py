from __future__ import annotations

import argparse
import asyncio
import json
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from detect_active_venues import detect_active_venues
from fetch_live_race import DomainThrottle, LiveSourceClient, fetch_and_save_race
from live_common import (
    CONFIG_PATH,
    ROOT,
    configure_logging,
    is_fetch_window,
    load_config,
    normalize_now,
    now_local,
    process_lock,
    resolve_root,
)
from select_target_races import select_target_races


async def _launch_browser(playwright: Any, config: dict[str, Any], logger: Any) -> Any:
    last_error = None
    for attempt in range(1, config["max_retries"] + 1):
        if not is_fetch_window(None, config):
            raise RuntimeError("fetch window closed before browser launch")
        try:
            return await playwright.chromium.launch(headless=True)
        except Exception as exc:
            last_error = exc
            logger.warning(str(exc), extra={"event": "browser_retry", "attempt": attempt})
            if attempt < config["max_retries"] and is_fetch_window(None, config):
                await asyncio.sleep((2 ** (attempt - 1)) + random.uniform(0.2, 1.0))
    raise RuntimeError(f"browser launch failed: {last_error}")


async def run_once(
    config: dict[str, Any],
    now: datetime | None = None,
    manifest_path: Path | None = None,
    output_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    current = normalize_now(now, config)
    logger = configure_logging(config)
    if not is_fetch_window(current, config):
        return {"status": "outside_window", "changed": False, "targets": []}
    today = current.date().isoformat()
    manifest = manifest_path or ROOT / "data" / "manifest.json"
    active = detect_active_venues(manifest, today)
    targets = []
    for venue in active:
        targets.extend(select_target_races(venue, current, config))
    if not targets:
        return {"status": "no_target_races", "changed": False, "active_venues": [v["slug"] for v in active], "targets": []}
    if dry_run:
        return {
            "status": "dry_run",
            "changed": False,
            "targets": [
                {
                    "venue": target["venue"],
                    "race_no": target["race_no"],
                    "deadline": target["deadline"],
                }
                for target in targets
            ],
        }

    from playwright.async_api import async_playwright

    target_root = output_root or resolve_root(config, "live_output_root")
    lock_path = resolve_root(config, "lock_path")
    by_venue: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for target in targets:
        by_venue[target["venue"]].append(target)
    semaphore = asyncio.Semaphore(config["max_parallel_venues"])
    throttle = DomainThrottle(float(config["domain_interval_seconds"]))
    results = []

    async with async_playwright() as playwright:
        browser = await _launch_browser(playwright, config, logger)
        try:
            async def run_venue(venue_targets: list[dict[str, Any]]) -> None:
                async with semaphore:
                    venue = venue_targets[0]["venue"]
                    venue_lock = lock_path.parent / "venues" / f"{venue}.lock"
                    with process_lock(venue_lock) as venue_acquired:
                        if not venue_acquired:
                            logger.info(
                                "venue fetch already running",
                                extra={"event": "venue_lock_skip", "venue": venue},
                            )
                            return
                        context = await browser.new_context(user_agent=config["source_user_agent"])
                        try:
                            client = LiveSourceClient(context, config, logger, throttle)
                            for target in venue_targets:
                                if not is_fetch_window(None, config):
                                    break
                                result = await fetch_and_save_race(client, target, target_root, config, logger)
                                results.append(result)
                        finally:
                            await context.close()

            await asyncio.wait_for(
                asyncio.gather(*(run_venue(items) for items in by_venue.values())),
                timeout=config["process_timeout_seconds"],
            )
        finally:
            await browser.close()
    return {
        "status": "completed",
        "changed": any(result["changed"] for result in results),
        "targets": [{"venue": item["venue"], "race_no": item["race_no"]} for item in targets],
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="test-only ISO timestamp")
    args = parser.parse_args()
    config = load_config(args.config)
    logger = configure_logging(config)
    current = datetime.fromisoformat(args.now) if args.now else None
    lock_path = resolve_root(config, "lock_path")
    with process_lock(lock_path) as acquired:
        if not acquired:
            logger.info("previous process still running", extra={"event": "process_lock_skip"})
            return 0
        result = asyncio.run(run_once(config, current, args.manifest, args.output_root, args.dry_run))
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] != "completed" or not any(item.get("error") for item in result.get("results", [])) else 1


if __name__ == "__main__":
    raise SystemExit(main())
