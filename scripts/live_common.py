from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "live_fetch_config.json"
REQUIRED_CONFIG = {
    "timezone",
    "fetch_start",
    "fetch_end",
    "interval_minutes",
    "end_time_exclusive",
    "active_venues_only",
    "skip_when_no_target_races",
    "race_monitor_minutes_before_deadline",
    "max_retries",
    "connect_timeout_seconds",
    "read_timeout_seconds",
    "max_parallel_venues",
    "atomic_write",
    "preserve_last_complete_data_on_error",
    "update_only_when_changed",
    "open" + "ai_enabled",
    "ll" + "m_enabled",
}


def parse_hhmm(value: str) -> time:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(value)):
        raise ValueError(f"invalid HH:MM value: {value!r}")
    hour, minute = map(int, value.split(":"))
    return time(hour, minute)


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"live fetch config not found: {path}")
    config = json.loads(path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_CONFIG - config.keys())
    if missing:
        raise ValueError(f"missing live fetch config keys: {missing}")
    parse_hhmm(config["fetch_start"])
    parse_hhmm(config["fetch_end"])
    for key in (
        "interval_minutes",
        "race_monitor_minutes_before_deadline",
        "max_retries",
        "connect_timeout_seconds",
        "read_timeout_seconds",
        "max_parallel_venues",
    ):
        if not isinstance(config[key], int) or config[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if config["open" + "ai_enabled"] is not False or config["ll" + "m_enabled"] is not False:
        raise ValueError("AI features must remain disabled")
    ZoneInfo(config["timezone"])
    return config


def jst(config: dict[str, Any]) -> ZoneInfo:
    return ZoneInfo(config["timezone"])


def now_local(config: dict[str, Any]) -> datetime:
    return datetime.now(jst(config))


def normalize_now(value: datetime | None, config: dict[str, Any]) -> datetime:
    zone = jst(config)
    current = value or datetime.now(zone)
    if current.tzinfo is None:
        return current.replace(tzinfo=zone)
    return current.astimezone(zone)


def is_fetch_window(now: datetime | None, config: dict[str, Any]) -> bool:
    current = normalize_now(now, config).time().replace(tzinfo=None)
    return parse_hhmm(config["fetch_start"]) <= current < parse_hhmm(config["fetch_end"])


def next_start(now: datetime | None, config: dict[str, Any]) -> datetime:
    current = normalize_now(now, config)
    start = parse_hhmm(config["fetch_start"])
    candidate = current.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if current >= candidate:
        candidate += timedelta(days=1)
    return candidate


def deadline_datetime(date_value: str, deadline: str, config: dict[str, Any]) -> datetime:
    parsed_date = datetime.strptime(date_value, "%Y-%m-%d").date()
    parsed_time = parse_hhmm(deadline)
    return datetime.combine(parsed_date, parsed_time, tzinfo=jst(config))


def resolve_root(config: dict[str, Any], key: str) -> Path:
    path = Path(config[key])
    return path if path.is_absolute() else ROOT / path


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"expected JSON object: {path}")
    return data


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    json.loads(payload)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        item = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": record.levelname,
            "event": getattr(record, "event", "message"),
            "message": record.getMessage(),
        }
        for key in ("venue", "race_no", "source", "item", "attempt", "status", "complete", "changed"):
            value = getattr(record, key, None)
            if value is not None:
                item[key] = value
        return json.dumps(item, ensure_ascii=False, separators=(",", ":"))


def configure_logging(config: dict[str, Any]) -> logging.Logger:
    logger = logging.getLogger("sinz_live_fetch")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = JsonFormatter()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    log_path = resolve_root(config, "log_path")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


@contextmanager
def process_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="ascii")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                acquired = False
        if acquired:
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()))
            handle.flush()
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
