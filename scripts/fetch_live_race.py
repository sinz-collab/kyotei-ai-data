from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fetch_direct_info import parse_direct_info
from fetch_exhibition import parse_exhibition
from fetch_odds import odds_difference, parse_odds
from fetch_original_exhibition import parse_original_exhibition
from live_common import atomic_write_json, is_fetch_window, load_json, now_local
from live_data_hash import content_hash
from validate_live_data import validate_live_data


RETRYABLE_HTTP = {429, 500, 502, 503, 504}


class DomainThrottle:
    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def wait(self) -> None:
        async with self.lock:
            delay = self.seconds - (time.monotonic() - self.last_request)
            if delay > 0:
                await asyncio.sleep(delay)
            self.last_request = time.monotonic()


def _base_document(
    target: dict[str, Any],
    source: str,
    status: str,
    complete: bool,
    data: dict[str, Any],
    error: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    document = {
        "date": target["date"],
        "venue": target["venue"],
        "race_no": target["race_no"],
        "deadline": target["deadline"],
        "fetched_at": now_local(config).isoformat(timespec="seconds"),
        "source": source,
        "status": status,
        "complete": complete,
        "content_hash": "",
        "error": error,
        "data": data,
    }
    document["content_hash"] = content_hash(document)
    return document


def _status(published: bool, complete: bool, cancelled: bool = False) -> str:
    if cancelled:
        return "cancelled"
    if complete:
        return "complete"
    return "partial" if published else "pending"


def save_document(
    path: Path,
    document: dict[str, Any],
    target: dict[str, Any],
    item_type: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    errors = validate_live_data(document, target, item_type)
    if errors:
        raise ValueError("; ".join(errors))
    previous = load_json(path) if path.is_file() else None
    previous_data = (previous or {}).get("data") or {}
    previous_has_data = bool(
        previous_data.get("entries")
        or previous_data.get("racers")
        or previous_data.get("odds")
        or any(
            value not in (None, "", [], {})
            for key, value in previous_data.items()
            if key not in {"difference", "missing_count"}
        )
    )
    if (
        config["preserve_last_complete_data_on_error"]
        and previous
        and (
            (
                previous.get("complete") is True
                and document.get("complete") is not True
            )
            or (
                previous.get("status") == "partial"
                and previous_has_data
                and document.get("status") in {"pending", "fetch_error", "parse_error"}
            )
        )
    ):
        return {"changed": False, "preserved": True, "document": previous}
    if previous and previous.get("content_hash") == document.get("content_hash"):
        return {"changed": False, "preserved": False, "document": previous}
    atomic_write_json(path, document)
    return {"changed": True, "preserved": False, "document": document}


class LiveSourceClient:
    def __init__(self, context: Any, config: dict[str, Any], logger: Any, throttle: DomainThrottle) -> None:
        self.context = context
        self.config = config
        self.logger = logger
        self.throttle = throttle

    async def _goto(self, page: Any, url: str, target: dict[str, Any], item: str) -> int | None:
        last_error: Exception | None = None
        for attempt in range(1, self.config["max_retries"] + 1):
            if not is_fetch_window(None, self.config):
                raise RuntimeError("fetch window closed; retry not started")
            await self.throttle.wait()
            try:
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=(
                        self.config["connect_timeout_seconds"]
                        + self.config["read_timeout_seconds"]
                    )
                    * 1000,
                )
                status = response.status if response else None
                if status in RETRYABLE_HTTP:
                    raise RuntimeError(f"retryable HTTP status {status}")
                if status is not None and status >= 400:
                    raise RuntimeError(f"HTTP status {status}")
                await page.wait_for_timeout(800)
                return status
            except Exception as exc:
                last_error = exc
                self.logger.warning(
                    str(exc),
                    extra={
                        "event": "fetch_retry",
                        "venue": target["venue"],
                        "race_no": target["race_no"],
                        "source": url,
                        "item": item,
                        "attempt": attempt,
                    },
                )
                if attempt >= self.config["max_retries"] or not is_fetch_window(None, self.config):
                    break
                backoff = min(30.0, (2 ** (attempt - 1)) + random.uniform(0.2, 1.2))
                await asyncio.sleep(backoff)
        raise RuntimeError(f"{item} fetch failed: {last_error}")

    async def fetch(self, target: dict[str, Any]) -> dict[str, Any]:
        page = await self.context.new_page()
        base = self.config["source_base_url"].rstrip("/")
        race_root = f"{base}/race/{target['venue']}/{target['date']}/{target['race_no']}R"
        source_last = f"{race_root}/last-minute"
        source_odds = f"{race_root}/odds"
        output: dict[str, Any] = {}
        try:
            await self._goto(page, source_last, target, "direct")
            body_text = await page.locator("body").inner_text()
            html_text = await page.content()
            cancelled = any(label in body_text for label in ("レース中止", "開催中止"))

            direct = parse_direct_info(html_text, body_text, target["race_no"])
            output["direct"] = _base_document(
                target,
                source_last,
                _status(direct.pop("_published"), direct.pop("_complete"), cancelled),
                False,
                direct,
                None,
                self.config,
            )
            output["direct"]["complete"] = output["direct"]["status"] == "complete"
            output["direct"]["content_hash"] = content_hash(output["direct"])

            exhibition = parse_exhibition(html_text, target["race_no"])
            output["exhibition"] = _base_document(
                target,
                source_last,
                _status(exhibition.pop("_published"), exhibition.pop("_complete"), cancelled),
                False,
                exhibition,
                None,
                self.config,
            )
            output["exhibition"]["complete"] = output["exhibition"]["status"] == "complete"
            output["exhibition"]["content_hash"] = content_hash(output["exhibition"])

            if cancelled:
                for item_type in ("original_exhibition", "odds"):
                    output[item_type] = _base_document(
                        target,
                        source_last,
                        "cancelled",
                        False,
                        {},
                        "race or meeting cancelled",
                        self.config,
                    )
                return output

            clicked = False
            for selector in (
                "button:has-text('オリジナル展示')",
                "a:has-text('オリジナル展示')",
                "text=オリジナル展示",
            ):
                locator = page.locator(selector).first
                if await locator.count():
                    try:
                        await locator.click(timeout=5000)
                        await page.wait_for_timeout(700)
                        clicked = True
                        break
                    except Exception:
                        continue
            original_html = await page.content()
            original_text = await page.locator("body").inner_text()
            original = parse_original_exhibition(original_html, original_text, target["race_no"])
            original["tab_clicked"] = clicked
            output["original_exhibition"] = _base_document(
                target,
                source_last,
                _status(original.pop("_published"), original.pop("_complete"), cancelled),
                False,
                original,
                None,
                self.config,
            )
            output["original_exhibition"]["complete"] = output["original_exhibition"]["status"] == "complete"
            output["original_exhibition"]["content_hash"] = content_hash(output["original_exhibition"])

            await self._goto(page, source_odds, target, "odds")
            odds_text = await page.locator("body").inner_text()
            odds = parse_odds(odds_text)
            output["odds"] = _base_document(
                target,
                source_odds,
                _status(odds.pop("_published"), odds.pop("_complete"), cancelled),
                False,
                odds,
                None,
                self.config,
            )
            output["odds"]["complete"] = output["odds"]["status"] == "complete"
            output["odds"]["content_hash"] = content_hash(output["odds"])
            return output
        finally:
            await page.close()


async def fetch_and_save_race(
    client: LiveSourceClient,
    target: dict[str, Any],
    output_root: Path,
    config: dict[str, Any],
    logger: Any,
) -> dict[str, Any]:
    race_dir = output_root / target["date"] / target["venue"] / f"{target['race_no']:02d}"
    started = time.monotonic()
    changed = False
    items: dict[str, Any] = {}
    try:
        documents = await asyncio.wait_for(
            client.fetch(target),
            timeout=config["race_timeout_seconds"],
        )
        previous_odds_path = race_dir / "odds.json"
        if "odds" in documents:
            previous = load_json(previous_odds_path) if previous_odds_path.is_file() else None
            documents["odds"]["data"]["difference"] = odds_difference(documents["odds"]["data"], previous)
            documents["odds"]["content_hash"] = content_hash(documents["odds"])
        for item_type, document in documents.items():
            result = save_document(
                race_dir / f"{item_type}.json",
                document,
                target,
                item_type,
                config,
            )
            changed = changed or result["changed"]
            items[item_type] = {
                "status": document["status"],
                "complete": document["complete"],
                "changed": result["changed"],
                "preserved": result["preserved"],
                "content_hash": result["document"].get("content_hash"),
            }
            logger.info(
                "live item processed",
                extra={
                    "event": "item_complete",
                    "venue": target["venue"],
                    "race_no": target["race_no"],
                    "item": item_type,
                    "status": document["status"],
                    "complete": document["complete"],
                    "changed": result["changed"],
                },
            )
        error = None
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error(
            error,
            extra={"event": "race_failed", "venue": target["venue"], "race_no": target["race_no"]},
        )
        for item_type in ("direct", "exhibition", "original_exhibition", "odds"):
            document = _base_document(
                target,
                config["source_base_url"],
                "fetch_error",
                False,
                {},
                error,
                config,
            )
            result = save_document(
                race_dir / f"{item_type}.json",
                document,
                target,
                item_type,
                config,
            )
            items[item_type] = {
                "status": "fetch_error",
                "complete": False,
                "changed": result["changed"],
                "preserved": result["preserved"],
                "content_hash": result["document"].get("content_hash"),
            }
    status_document = {
        "date": target["date"],
        "venue": target["venue"],
        "race_no": target["race_no"],
        "deadline": target["deadline"],
        "fetched_at": now_local(config).isoformat(timespec="seconds"),
        "source": config["source_base_url"],
        "status": "fetch_error" if error else "complete",
        "complete": bool(items) and all(item["complete"] for item in items.values()),
        "content_hash": "",
        "error": error,
        "data": {
            "items": items,
            "changed": changed,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    }
    if not error and not status_document["complete"]:
        status_document["status"] = "partial"
    status_document["content_hash"] = content_hash(status_document)
    atomic_write_json(race_dir / "status.json", status_document)
    return {"target": target, "changed": changed, "error": error, "items": items}
