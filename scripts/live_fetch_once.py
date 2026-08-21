from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
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
from sync_morning_data import ensure_current_morning_data


PUBLISHER_REPO = Path("/opt/sinz-edge/runtime/publisher-repo")
HEIWAJIMA_LIVE_APPLIER = PUBLISHER_REPO / "automation" / "apply_heiwajima_live_v1.py"
HEIWAJIMA_DATA_ROOT = PUBLISHER_REPO / "data"
ASHIYA_LIVE_APPLIER = PUBLISHER_REPO / "automation" / "apply_ashiya_live_v1.py"
ASHIYA_LIVE_RUNNER = PUBLISHER_REPO / "automation" / "run_ashiya_v16_live.py"
ASHIYA_DATA_ROOT = PUBLISHER_REPO / "data"
TODA_LIVE_APPLIER = PUBLISHER_REPO / "automation" / "apply_toda_live_v5.py"
TODA_DATA_ROOT = PUBLISHER_REPO / "data"
BIWAKO_LIVE_RUNNER = PUBLISHER_REPO / "automation" / "run_biwako_v1_1.py"
BIWAKO_DATA_ROOT = PUBLISHER_REPO / "data"

SHIMONOSEKI_LIVE_APPLIER = ROOT / "automation" / "apply_shimonoseki_live_v6.py"
SHIMONOSEKI_DATA_ROOT = PUBLISHER_REPO / "data"


def stage_tokoname_preliminary_after_morning_sync(
    config: dict[str, Any],
    target_date: str,
    manifest_path: Path,
    live_root: Path,
    logger: Any,
    *,
    prediction_output_root: Path | None = None,
) -> dict[str, Any] | None:
    morning_root = resolve_root(config, "morning_data_root")
    output_root = prediction_output_root or ROOT / "runtime" / "predictions"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        venue = next(
            (
                item
                for item in manifest.get("venues") or []
                if item.get("slug") == "tokoname"
            ),
            None,
        )
        if (
            manifest.get("date") != target_date
            or not venue
            or venue.get("open") is not True
            or venue.get("date") != target_date
        ):
            return None

        data_path = str(venue.get("dataPath") or "")
        if not data_path or not (morning_root / data_path).is_file():
            return None

        from stage_tokoname_predictions import stage_tokoname_predictions

        report = stage_tokoname_predictions(
            target_date,
            morning_root=morning_root,
            live_root=live_root,
            output_root=output_root,
        )
        if report.get("status") == "preliminary":
            logger.info(
                json.dumps(report, ensure_ascii=False),
                extra={
                    "event": "tokoname_preliminary_staged",
                    "venue": "tokoname",
                    "phase": "preliminary",
                },
            )
        return report
    except Exception as exc:
        logger.error(
            f"{type(exc).__name__}: {exc}",
            extra={
                "event": "tokoname_preliminary_staging_failed",
                "venue": "tokoname",
            },
        )
        return {
            "status": "error",
            "date": target_date,
            "written": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def apply_heiwajima_live_prediction(
    target: dict[str, Any],
    race_dir: Path,
    fetch_result: dict[str, Any],
    logger: Any,
) -> None:
    if target.get("venue") != "heiwajima":
        return
    if fetch_result.get("error"):
        return

    items = fetch_result.get("items") or {}
    required = ("direct", "exhibition", "original_exhibition")
    if not all((items.get(name) or {}).get("complete") is True for name in required):
        return

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(HEIWAJIMA_LIVE_APPLIER),
        "--date",
        str(target["date"]),
        "--race",
        str(target["race_no"]),
        "--data-root",
        str(HEIWAJIMA_DATA_ROOT),
        "--live-root",
        str(race_dir),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(
            stderr.decode("utf-8", errors="replace").strip(),
            extra={
                "event": "heiwajima_live_prediction_failed",
                "venue": target["venue"],
                "race_no": target["race_no"],
            },
        )
        return

    logger.info(
        stdout.decode("utf-8", errors="replace").strip(),
        extra={
            "event": "heiwajima_live_prediction_complete",
            "venue": target["venue"],
            "race_no": target["race_no"],
        },
    )


async def apply_ashiya_live_data(
    target: dict[str, Any],
    race_dir: Path,
    fetch_result: dict[str, Any],
    logger: Any,
) -> None:
    if target.get("venue") != "ashiya":
        return
    if fetch_result.get("error"):
        return

    items = fetch_result.get("items") or {}
    required = ("direct", "exhibition", "original_exhibition")

    if not all(
        (items.get(name) or {}).get("complete") is True
        and (items.get(name) or {}).get("status") == "complete"
        for name in required
    ):
        return

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ASHIYA_LIVE_APPLIER),
        "--date",
        str(target["date"]),
        "--race",
        str(target["race_no"]),
        "--data-root",
        str(ASHIYA_DATA_ROOT),
        "--live-root",
        str(race_dir),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(
            stderr.decode("utf-8", errors="replace").strip(),
            extra={
                "event": "ashiya_live_apply_failed",
                "venue": target["venue"],
                "race_no": target["race_no"],
            },
        )
        return

    logger.info(
        stdout.decode("utf-8", errors="replace").strip(),
        extra={
            "event": "ashiya_live_applied",
            "venue": target["venue"],
            "race_no": target["race_no"],
        },
    )

    prediction_process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ASHIYA_LIVE_RUNNER),
        "--date",
        str(target["date"]),
        "--race",
        str(target["race_no"]),
        "--data-root",
        str(ASHIYA_DATA_ROOT),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    prediction_stdout, prediction_stderr = (
        await prediction_process.communicate()
    )

    if prediction_process.returncode != 0:
        logger.error(
            prediction_stderr.decode(
                "utf-8",
                errors="replace",
            ).strip(),
            extra={
                "event": "ashiya_live_prediction_failed",
                "venue": target["venue"],
                "race_no": target["race_no"],
            },
        )
        return

    logger.info(
        prediction_stdout.decode(
            "utf-8",
            errors="replace",
        ).strip(),
        extra={
            "event": "ashiya_live_prediction_complete",
            "venue": target["venue"],
            "race_no": target["race_no"],
        },
    )


async def apply_toda_live_prediction(
    target: dict[str, Any],
    race_dir: Path,
    fetch_result: dict[str, Any],
    logger: Any,
) -> None:
    if target.get("venue") != "toda":
        return
    if fetch_result.get("error"):
        return

    items = fetch_result.get("items") or {}
    required = ("direct", "exhibition")
    if not all(
        (items.get(name) or {}).get("complete") is True
        and (items.get(name) or {}).get("status") == "complete"
        for name in required
    ):
        return

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(TODA_LIVE_APPLIER),
        "--date",
        str(target["date"]),
        "--race",
        str(target["race_no"]),
        "--data-root",
        str(TODA_DATA_ROOT),
        "--live-root",
        str(race_dir),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(
            stderr.decode("utf-8", errors="replace").strip(),
            extra={
                "event": "toda_live_prediction_failed",
                "venue": target["venue"],
                "race_no": target["race_no"],
            },
        )
        return

    logger.info(
        stdout.decode("utf-8", errors="replace").strip(),
        extra={
            "event": "toda_live_prediction_complete",
            "venue": target["venue"],
            "race_no": target["race_no"],
        },
    )


async def apply_biwako_live_prediction(
    target: dict[str, Any],
    race_dir: Path,
    fetch_result: dict[str, Any],
    logger: Any,
) -> None:
    if target.get("venue") != "biwako" or fetch_result.get("error"):
        return

    items = fetch_result.get("items") or {}
    required = ("direct", "exhibition", "original_exhibition")
    if not all(
        (items.get(name) or {}).get("complete") is True
        and (items.get(name) or {}).get("status") == "complete"
        for name in required
    ):
        return

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(BIWAKO_LIVE_RUNNER),
        "--date",
        str(target["date"]),
        "--stage",
        "final",
        "--race",
        str(target["race_no"]),
        "--data-root",
        str(BIWAKO_DATA_ROOT),
        "--live-root",
        str(race_dir),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(
            stderr.decode("utf-8", errors="replace").strip(),
            extra={
                "event": "biwako_live_prediction_failed",
                "venue": target["venue"],
                "race_no": target["race_no"],
            },
        )
        return

    logger.info(
        stdout.decode("utf-8", errors="replace").strip(),
        extra={
            "event": "biwako_live_prediction_complete",
            "venue": target["venue"],
            "race_no": target["race_no"],
        },
    )


async def apply_shimonoseki_live_prediction(
    target: dict[str, Any],
    race_dir: Path,
    fetch_result: dict[str, Any],
    logger: Any,
) -> None:
    if target.get("venue") != "shimonoseki" or fetch_result.get("error"):
        return
    items = fetch_result.get("items") or {}
    required = ("direct", "exhibition", "original_exhibition")
    if not all(
        (items.get(name) or {}).get("complete") is True
        and (items.get(name) or {}).get("status") == "complete"
        for name in required
    ):
        return
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(SHIMONOSEKI_LIVE_APPLIER),
        "--date", str(target["date"]),
        "--race", str(target["race_no"]),
        "--data-root", str(SHIMONOSEKI_DATA_ROOT),
        "--live-root", str(race_dir),
        cwd=str(PUBLISHER_REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        logger.error(
            stderr.decode("utf-8", errors="replace").strip(),
            extra={"event":"shimonoseki_live_prediction_failed","venue":"shimonoseki","race_no":target["race_no"]},
        )
        return
    logger.info(
        stdout.decode("utf-8", errors="replace").strip(),
        extra={"event":"shimonoseki_live_prediction_complete","venue":"shimonoseki","race_no":target["race_no"],"odds_used_for_probability":False},
    )

def stage_tokoname_results(
    config: dict[str, Any],
    target_date: str,
    live_root: Path,
    results: list[dict[str, Any]],
    logger: Any,
) -> dict[str, Any] | None:
    live_items = {"direct", "exhibition", "odds"}
    race_numbers = sorted(
        {
            int(result["target"]["race_no"])
            for result in results
            if result.get("target", {}).get("venue") == "tokoname"
            and all(
                (result.get("items", {}).get(item) or {}).get("complete") is True
                and (result.get("items", {}).get(item) or {}).get("status") == "complete"
                for item in live_items
            )
        }
    )
    if not race_numbers:
        return None
    try:
        from stage_tokoname_predictions import stage_tokoname_predictions

        report = stage_tokoname_predictions(
            target_date,
            morning_root=resolve_root(config, "morning_data_root"),
            live_root=live_root,
            output_root=ROOT / "runtime" / "predictions",
            race_numbers=race_numbers,
        )
        if report.get("engine_invoked_races"):
            logger.info(
                json.dumps(report, ensure_ascii=False),
                extra={
                    "event": "tokoname_existing_engine_recalculated",
                    "venue": "tokoname",
                    "phase": "final",
                    "races": report["engine_invoked_races"],
                    "odds_used_for_probability": False,
                },
            )
        return report
    except Exception as exc:
        logger.error(
            f"{type(exc).__name__}: {exc}",
            extra={"event": "tokoname_prediction_staging_failed", "venue": "tokoname"},
        )
        return {
            "status": "error",
            "date": target_date,
            "requested_races": race_numbers,
            "written": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


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
    target_root = output_root or resolve_root(config, "live_output_root")
    manifest = manifest_path or ensure_current_morning_data(config, today, logger)
    if not dry_run:
        stage_tokoname_preliminary_after_morning_sync(
            config,
            today,
            manifest,
            target_root,
            logger,
        )
    active = detect_active_venues(manifest, today)
    targets = []
    for venue in active:
        targets.extend(select_target_races(venue, current, config, target_root))
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
                                race_dir = (
                                    target_root
                                    / target["date"]
                                    / target["venue"]
                                    / f"{target['race_no']:02d}"
                                )
                                await apply_heiwajima_live_prediction(
                                    target,
                                    race_dir,
                                    result,
                                    logger,
                                )
                                await apply_ashiya_live_data(
                                    target,
                                    race_dir,
                                    result,
                                    logger,
                                )
                                await apply_toda_live_prediction(
                                    target,
                                    race_dir,
                                    result,
                                    logger,
                                )
                                await apply_biwako_live_prediction(
                                    target,
                                    race_dir,
                                    result,
                                    logger,
                                )
                                await apply_shimonoseki_live_prediction(
                                    target,
                                    race_dir,
                                    result,
                                    logger,
                                )
                        finally:
                            await context.close()

            await asyncio.wait_for(
                asyncio.gather(*(run_venue(items) for items in by_venue.values())),
                timeout=config["process_timeout_seconds"],
            )
        finally:
            await browser.close()
    response = {
        "status": "completed",
        "changed": any(result["changed"] for result in results),
        "targets": [{"venue": item["venue"], "race_no": item["race_no"]} for item in targets],
        "results": results,
    }
    tokoname_staging = stage_tokoname_results(
        config,
        today,
        target_root,
        results,
        logger,
    )
    if tokoname_staging is not None:
        response["tokoname_prediction_staging"] = tokoname_staging
    return response


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
