#!/usr/bin/env python3
"""Official-result based six-month racer/course statistics foundation.

This module is deliberately independent from prediction and publishing code.  It
downloads the official daily K archive, keeps an auditable normalized archive in
SQLite, and maintains a small rolling aggregate with additive/subtractive daily
buckets.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "player_course_6m" / "player_course_6m.sqlite"
DEFAULT_EXPORT = ROOT / "data" / "player_course_6m" / "player_course_6m.csv"
DEFAULT_STATUS = ROOT / "data" / "player_course_6m" / "last_update.json"
OFFICIAL_URL = "https://www1.mbrace.or.jp/od2/K/{month}/k{ymd}.lzh"
JST = timezone(timedelta(hours=9))
TACTICS = ("まくり差し", "逃げ", "差し", "まくり", "抜き", "恵まれ")
COUNTED_STARTS = {"1", "2", "3", "4", "5", "6", "F", "L1", "S1", "S2", "K1"}
ST_FINISHES = {"1", "2", "3", "4", "5", "6", "S0", "S1", "S2"}
FINISH_FIELDS = tuple(str(n) for n in range(1, 7))
KIMARITE_FIELDS = ("逃げ", "差し", "まくり", "まくり差し", "抜き", "恵まれ")
COUNTER_FIELDS = (
    "starts", "win", "top2", "top3", "st_count", "st_sum_millis",
    "finish1", "finish2", "finish3", "finish4", "finish5", "finish6",
    "f_count", "l1_count", "s1_count", "s2_count", "k1_count",
    "kimarite_escape", "kimarite_sashi", "kimarite_makuri",
    "kimarite_makurizashi", "kimarite_nuki", "kimarite_megumi",
)
KIMARITE_COLUMN = {
    "逃げ": "kimarite_escape", "差し": "kimarite_sashi", "まくり": "kimarite_makuri",
    "まくり差し": "kimarite_makurizashi", "抜き": "kimarite_nuki", "恵まれ": "kimarite_megumi",
}


@dataclass(frozen=True)
class ResultRow:
    race_date: str
    venue: str
    race_no: int
    lane: int
    reg_no: str
    racer_name: str
    motor_no: int | None
    boat_no: int | None
    actual_course: int | None
    finish_code: str
    st_text: str | None
    st_millis: int | None
    kimarite: str | None
    race_formed: int


def jst_now() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


def rolling_window_start(as_of: date) -> date:
    """Recent six months as displayed by kyoteidb: current month + prior 5."""
    year = as_of.year
    month = as_of.month - 5
    while month <= 0:
        year -= 1
        month += 12
    return date(year, month, 1)


def iter_dates(start: date, end: date) -> Iterator[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def normalize_finish(token: str) -> str:
    token = token.strip().upper()
    if token.startswith("0") and token[1:].isdigit():
        token = str(int(token))
    return token


def parse_st(token: str | None) -> tuple[str | None, int | None]:
    if not token:
        return None, None
    text = token.strip().upper()
    match = re.fullmatch(r"([FL])?(\d+)\.(\d+)", text)
    if not match:
        return text or None, None
    whole, fraction = int(match.group(2)), match.group(3)
    millis = whole * 1000 + int((fraction + "000")[:3])
    if match.group(1) == "F":
        millis = -millis
    return text, millis


def _parse_result_line(line: str) -> dict[str, object] | None:
    head = re.match(
        r"^\s*(?P<finish>0[1-6]|[1-6]|F|L[01]?|S[012]|K[01])\s+"
        r"(?P<lane>[1-6])\s+(?P<reg>\d{4})\s+(?P<body>.+)$",
        line,
    )
    if not head:
        return None
    body = head.group("body")
    # The name is fixed-width in the source but reluctant matching before two
    # numeric equipment fields is more tolerant of historical layout changes.
    detail = re.match(
        r"(?P<name>.+?)\s+(?P<motor>\d{1,3})\s+(?P<boat>\d{1,3})\s+(?P<tail>.+)$",
        body,
    )
    if not detail:
        return None
    tail = detail.group("tail")
    normal = re.match(
        r"(?P<exhibit>\d+\.\d+|[A-Z]\s*\.)\s+"
        r"(?P<course>[1-6]|[A-Z])(?:\s*\.)?\s+"
        r"(?P<st>[FL]?\d+\.\d+|\.)",
        tail,
    )
    course: int | None = None
    st_token: str | None = None
    if normal:
        if normal.group("course").isdigit():
            course = int(normal.group("course"))
        st_token = normal.group("st")
    return {
        "finish_code": normalize_finish(head.group("finish")),
        "lane": int(head.group("lane")),
        "reg_no": head.group("reg"),
        "racer_name": re.sub(r"[\s\u3000]+", "", detail.group("name")),
        "motor_no": int(detail.group("motor")),
        "boat_no": int(detail.group("boat")),
        "actual_course": course,
        "st_token": st_token,
    }


def parse_k_text(raw: bytes, expected_date: date | None = None) -> list[ResultRow]:
    text = raw.decode("cp932", errors="replace")
    sections = re.split(r"(?m)^\d{2}KBGN\s*$", text)[1:]
    parsed: list[dict[str, object]] = []
    parse_errors: list[str] = []
    for section in sections:
        date_match = re.search(r"(20\d{2})/\s*(\d{1,2})/\s*(\d{1,2})", section)
        venue_match = re.search(r"ボートレース([^\r\n]+)", section)
        if not date_match or not venue_match:
            continue
        race_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))
        if expected_date and race_date != expected_date:
            raise ValueError(f"archive date mismatch: expected {expected_date}, found {race_date}")
        venue = re.sub(r"[\s\u3000]+", "", venue_match.group(1)).strip("［[成績］]")
        race_no: int | None = None
        tactic: str | None = None
        race_rows: list[dict[str, object]] = []
        for line in section.splitlines():
            race_match = re.match(r"^\s*(\d{1,2})R\s+", line)
            if race_match and "H" in line:
                if race_rows:
                    for item in race_rows:
                        item["kimarite"] = tactic if item["finish_code"] == "1" else None
                    parsed.extend(race_rows)
                race_no = int(race_match.group(1))
                tactic = None
                race_rows = []
                continue
            if race_no is None:
                continue
            for candidate in TACTICS:
                if candidate in line:
                    tactic = candidate
                    break
            item = _parse_result_line(line)
            if item:
                item.update(race_date=race_date.isoformat(), venue=venue, race_no=race_no)
                race_rows.append(item)
            elif re.match(r"^\s*(?:0[1-6]|[1-6]|F|L[01]?|S[012]|K[01])\s+", line):
                parse_errors.append(line)
        if race_rows:
            for item in race_rows:
                item["kimarite"] = tactic if item["finish_code"] == "1" else None
            parsed.extend(race_rows)
    if parse_errors:
        raise ValueError(f"unparsed result rows: {len(parse_errors)}; first={parse_errors[0]!r}")
    if not parsed:
        raise ValueError("no result rows parsed from official K file")

    formed: dict[tuple[str, str, int], bool] = defaultdict(bool)
    for item in parsed:
        key = (str(item["race_date"]), str(item["venue"]), int(item["race_no"]))
        formed[key] = formed[key] or str(item["finish_code"]) in FINISH_FIELDS
    rows: list[ResultRow] = []
    for item in parsed:
        st_text, st_millis = parse_st(item.get("st_token") if isinstance(item.get("st_token"), str) else None)
        key = (str(item["race_date"]), str(item["venue"]), int(item["race_no"]))
        rows.append(ResultRow(
            race_date=str(item["race_date"]), venue=str(item["venue"]), race_no=int(item["race_no"]),
            lane=int(item["lane"]), reg_no=str(item["reg_no"]), racer_name=str(item["racer_name"]),
            motor_no=item["motor_no"] if isinstance(item["motor_no"], int) else None,
            boat_no=item["boat_no"] if isinstance(item["boat_no"], int) else None,
            actual_course=item["actual_course"] if isinstance(item["actual_course"], int) else None,
            finish_code=str(item["finish_code"]), st_text=st_text, st_millis=st_millis,
            kimarite=item.get("kimarite") if isinstance(item.get("kimarite"), str) else None,
            race_formed=int(formed[key]),
        ))
    return rows


def _load_lhafile():
    try:
        import lhafile  # type: ignore
        return lhafile
    except ImportError as exc:
        raise RuntimeError("lhafile is required: pip install -r automation/requirements.txt") from exc


def extract_lzh(payload: bytes) -> bytes:
    lhafile = _load_lhafile()
    with tempfile.NamedTemporaryFile(suffix=".lzh", delete=False) as handle:
        handle.write(payload)
        temp_path = Path(handle.name)
    try:
        archive = lhafile.Lhafile(str(temp_path))
        names = [name for name in archive.namelist() if name.upper().endswith(".TXT")]
        if len(names) != 1:
            raise ValueError(f"expected one TXT member, got {names}")
        return archive.read(names[0])
    finally:
        with contextlib.suppress(OSError):
            temp_path.unlink()


def fetch_official(day: date, timeout: float = 20.0, retries: int = 2) -> tuple[bytes, str, str]:
    ymd = day.strftime("%y%m%d")
    url = OFFICIAL_URL.format(month=day.strftime("%Y%m"), ymd=ymd)
    request = urllib.request.Request(url, headers={"User-Agent": "kyotei-data-foundation/1.0"})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
            if len(payload) < 100:
                raise ValueError(f"official archive too small: {len(payload)} bytes")
            return extract_lzh(payload), url, hashlib.sha256(payload).hexdigest()
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {day} from {url}: {last_error}")


def connect_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    create_schema(con)
    return con


def create_schema(con: sqlite3.Connection) -> None:
    counters = ",\n".join(f"{field} INTEGER NOT NULL DEFAULT 0" for field in COUNTER_FIELDS)
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS source_days (
            race_date TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            archive_sha256 TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            race_count INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('ok', 'error')),
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS race_results (
            race_date TEXT NOT NULL,
            venue TEXT NOT NULL,
            race_no INTEGER NOT NULL,
            lane INTEGER NOT NULL,
            reg_no TEXT NOT NULL,
            racer_name TEXT NOT NULL,
            motor_no INTEGER,
            boat_no INTEGER,
            actual_course INTEGER,
            finish_code TEXT NOT NULL,
            st_text TEXT,
            st_millis INTEGER,
            kimarite TEXT,
            race_formed INTEGER NOT NULL,
            PRIMARY KEY (race_date, venue, race_no, lane)
        );
        CREATE INDEX IF NOT EXISTS idx_results_reg_course_date
            ON race_results(reg_no, actual_course, race_date);
        CREATE TABLE IF NOT EXISTS daily_course_stats (
            race_date TEXT NOT NULL,
            reg_no TEXT NOT NULL,
            actual_course INTEGER NOT NULL,
            racer_name TEXT NOT NULL,
            {counters},
            PRIMARY KEY (race_date, reg_no, actual_course)
        );
        CREATE TABLE IF NOT EXISTS rolling_course_stats (
            reg_no TEXT NOT NULL,
            actual_course INTEGER NOT NULL,
            racer_name TEXT NOT NULL,
            {counters},
            PRIMARY KEY (reg_no, actual_course)
        );
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    con.commit()


def _counter_for_row(row: ResultRow) -> dict[str, int]:
    values = {field: 0 for field in COUNTER_FIELDS}
    if not row.race_formed or row.actual_course not in range(1, 7):
        return values
    finish = row.finish_code
    if finish in COUNTED_STARTS:
        values["starts"] = 1
    if finish in FINISH_FIELDS:
        rank = int(finish)
        values[f"finish{rank}"] = 1
        values["win"] = int(rank == 1)
        values["top2"] = int(rank <= 2)
        values["top3"] = int(rank <= 3)
    if finish == "F": values["f_count"] = 1
    if finish == "L1": values["l1_count"] = 1
    if finish == "S1": values["s1_count"] = 1
    if finish == "S2": values["s2_count"] = 1
    if finish == "K1": values["k1_count"] = 1
    if finish in ST_FINISHES and row.st_millis is not None:
        values["st_count"] = 1
        values["st_sum_millis"] = row.st_millis
    if finish == "1" and row.kimarite in KIMARITE_COLUMN:
        values[KIMARITE_COLUMN[row.kimarite]] = 1
    return values


def build_daily_rows(rows: Sequence[ResultRow]) -> list[tuple[object, ...]]:
    grouped: dict[tuple[str, str, int], dict[str, object]] = {}
    for row in rows:
        if row.actual_course not in range(1, 7):
            continue
        key = (row.race_date, row.reg_no, row.actual_course)
        if key not in grouped:
            grouped[key] = {"racer_name": row.racer_name, **{field: 0 for field in COUNTER_FIELDS}}
        values = _counter_for_row(row)
        for field in COUNTER_FIELDS:
            grouped[key][field] = int(grouped[key][field]) + values[field]
    return [
        (day, reg, course, str(values["racer_name"]), *(int(values[field]) for field in COUNTER_FIELDS))
        for (day, reg, course), values in grouped.items()
    ]


def ingest_day(con: sqlite3.Connection, day: date, rows: Sequence[ResultRow], url: str, sha256: str) -> None:
    day_text = day.isoformat()
    if any(row.race_date != day_text for row in rows):
        raise ValueError(f"parsed rows contain a date other than {day_text}")
    races = {(r.venue, r.race_no) for r in rows}
    if len(rows) < 6 or len(rows) > 24 * 12 * 6:
        raise ValueError(f"implausible row count for {day_text}: {len(rows)}")
    daily = build_daily_rows(rows)
    placeholders = ",".join("?" for _ in range(14))
    counter_placeholders = ",".join("?" for _ in range(4 + len(COUNTER_FIELDS)))
    with con:
        con.execute("DELETE FROM race_results WHERE race_date=?", (day_text,))
        con.execute("DELETE FROM daily_course_stats WHERE race_date=?", (day_text,))
        con.executemany(
            f"INSERT INTO race_results VALUES ({placeholders})",
            [(
                r.race_date, r.venue, r.race_no, r.lane, r.reg_no, r.racer_name,
                r.motor_no, r.boat_no, r.actual_course, r.finish_code, r.st_text,
                r.st_millis, r.kimarite, r.race_formed,
            ) for r in rows],
        )
        con.executemany(f"INSERT INTO daily_course_stats VALUES ({counter_placeholders})", daily)
        con.execute(
            "INSERT OR REPLACE INTO source_days VALUES (?,?,?,?,?,?,?,NULL)",
            (day_text, url, sha256, jst_now(), len(rows), len(races), "ok"),
        )


def set_metadata(con: sqlite3.Connection, **values: str) -> None:
    con.executemany("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", values.items())


def rebuild_rolling(con: sqlite3.Connection, as_of: date) -> None:
    window_start = rolling_window_start(as_of).isoformat()
    fields = ",".join(COUNTER_FIELDS)
    sums = ",".join(f"SUM({field})" for field in COUNTER_FIELDS)
    with con:
        con.execute("DELETE FROM rolling_course_stats")
        con.execute(f"""
            INSERT INTO rolling_course_stats(reg_no,actual_course,racer_name,{fields})
            SELECT reg_no, actual_course, MAX(racer_name), {sums}
            FROM daily_course_stats
            WHERE race_date BETWEEN ? AND ?
            GROUP BY reg_no, actual_course
        """, (window_start, as_of.isoformat()))
        con.execute("DELETE FROM rolling_course_stats WHERE starts<=0 AND st_count<=0")
        set_metadata(con, rolling_as_of=as_of.isoformat(), rolling_window_start=window_start,
                     rolling_updated_at=jst_now())


def _apply_daily_delta(con: sqlite3.Connection, day_text: str, sign: int) -> None:
    fields = ",".join(COUNTER_FIELDS)
    signed = ",".join(f"{sign}*{field}" for field in COUNTER_FIELDS)
    updates = ",".join(f"{field}={field}+excluded.{field}" for field in COUNTER_FIELDS)
    con.execute(f"""
        INSERT INTO rolling_course_stats(reg_no,actual_course,racer_name,{fields})
        SELECT reg_no,actual_course,racer_name,{signed}
        FROM daily_course_stats WHERE race_date=?
        ON CONFLICT(reg_no,actual_course) DO UPDATE SET
          racer_name=excluded.racer_name,{updates}
    """, (day_text,))


def differential_refresh(con: sqlite3.Connection, as_of: date, replaced_day_old: list[sqlite3.Row] | None = None) -> None:
    old_as_of_row = con.execute("SELECT value FROM metadata WHERE key='rolling_as_of'").fetchone()
    old_start_row = con.execute("SELECT value FROM metadata WHERE key='rolling_window_start'").fetchone()
    if not old_as_of_row or not old_start_row:
        rebuild_rolling(con, as_of)
        return
    old_as_of = parse_iso_date(old_as_of_row[0])
    old_start = parse_iso_date(old_start_row[0])
    new_start = rolling_window_start(as_of)
    if as_of < old_as_of or as_of > old_as_of + timedelta(days=1):
        rebuild_rolling(con, as_of)
        return
    with con:
        # If an already-included day was corrected, subtract its captured old buckets.
        if replaced_day_old:
            fields = ",".join(COUNTER_FIELDS)
            placeholders = ",".join("?" for _ in range(4 + len(COUNTER_FIELDS)))
            con.execute("CREATE TEMP TABLE IF NOT EXISTS old_daily AS SELECT * FROM daily_course_stats WHERE 0")
            con.execute("DELETE FROM old_daily")
            con.executemany(f"INSERT INTO old_daily VALUES ({placeholders})", [tuple(row) for row in replaced_day_old])
            signed = ",".join(f"-1*{field}" for field in COUNTER_FIELDS)
            updates = ",".join(f"{field}={field}+excluded.{field}" for field in COUNTER_FIELDS)
            con.execute(f"""
                INSERT INTO rolling_course_stats(reg_no,actual_course,racer_name,{fields})
                SELECT reg_no,actual_course,racer_name,{signed} FROM old_daily WHERE 1
                ON CONFLICT(reg_no,actual_course) DO UPDATE SET {updates}
            """)
        if new_start > old_start:
            for expired in iter_dates(old_start, new_start - timedelta(days=1)):
                _apply_daily_delta(con, expired.isoformat(), -1)
        _apply_daily_delta(con, as_of.isoformat(), 1)
        con.execute("DELETE FROM rolling_course_stats WHERE starts<=0 AND st_count<=0")
        set_metadata(con, rolling_as_of=as_of.isoformat(), rolling_window_start=new_start.isoformat(),
                     rolling_updated_at=jst_now())


def download_and_ingest(con: sqlite3.Connection, day: date, *, timeout: float, retries: int) -> int:
    raw, url, digest = fetch_official(day, timeout=timeout, retries=retries)
    rows = parse_k_text(raw, expected_date=day)
    ingest_day(con, day, rows, url, digest)
    return len(rows)


def missing_days(con: sqlite3.Connection, start: date, end: date) -> list[str]:
    present = {row[0] for row in con.execute(
        "SELECT race_date FROM source_days WHERE status='ok' AND race_date BETWEEN ? AND ?",
        (start.isoformat(), end.isoformat()),
    )}
    return [day.isoformat() for day in iter_dates(start, end) if day.isoformat() not in present]


def duplicate_count(con: sqlite3.Connection) -> int:
    return int(con.execute("""
        SELECT COUNT(*) FROM (
          SELECT race_date,venue,race_no,lane,COUNT(*) n FROM race_results
          GROUP BY race_date,venue,race_no,lane HAVING n>1
        )
    """).fetchone()[0])


def pct(numerator: int, denominator: int) -> float | None:
    return (100.0 * numerator / denominator) if denominator else None


def round_half_up(value: float, digits: int) -> float:
    quant = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def export_rows(con: sqlite3.Connection) -> list[dict[str, object]]:
    meta = dict(con.execute("SELECT key,value FROM metadata"))
    result: list[dict[str, object]] = []
    for row in con.execute("SELECT * FROM rolling_course_stats ORDER BY reg_no,actual_course"):
        item = dict(row)
        starts = int(item["starts"])
        st_count = int(item["st_count"])
        average_st = round_half_up(int(item["st_sum_millis"]) / st_count / 1000, 2) if st_count else None
        result.append({
            "as_of": meta.get("rolling_as_of"), "window_start": meta.get("rolling_window_start"),
            "reg_no": item["reg_no"], "racer_name": item["racer_name"],
            "actual_course": item["actual_course"], "starts": starts,
            "win_rate": pct(int(item["win"]), starts),
            "top2_rate": pct(int(item["top2"]), starts),
            "top3_rate": pct(int(item["top3"]), starts),
            "average_st": average_st, "st_count": st_count,
            "kimarite_escape": item["kimarite_escape"], "kimarite_sashi": item["kimarite_sashi"],
            "kimarite_makuri": item["kimarite_makuri"],
            "kimarite_makurizashi": item["kimarite_makurizashi"],
            "kimarite_nuki": item["kimarite_nuki"], "kimarite_megumi": item["kimarite_megumi"],
            "finish1": item["finish1"], "finish2": item["finish2"], "finish3": item["finish3"],
            "finish4": item["finish4"], "finish5": item["finish5"], "finish6": item["finish6"],
            "f_count": item["f_count"], "l1_count": item["l1_count"],
            "s1_count": item["s1_count"], "s2_count": item["s2_count"], "k1_count": item["k1_count"],
        })
    return result


def write_export(con: sqlite3.Connection, path: Path) -> int:
    rows = export_rows(con)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["as_of"])
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)
    return len(rows)


def write_status(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table": self._table = []
        elif tag == "tr" and self._table is not None: self._row = []
        elif tag in {"th", "td"} and self._row is not None: self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None: self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip()); self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row: self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table); self._table = None


def fetch_kyoteidb_course(reg_no: str, timeout: float = 20.0) -> tuple[list[list[str]], list[list[str]], str]:
    url = f"https://kyoteidb.com/racer/{reg_no}/course"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        html = response.read().decode("utf-8", errors="replace")
    parser = _TableParser(); parser.feed(html)
    stats = [t for t in parser.tables if t and t[0][:7] == ["コース", "出走", "勝率", "1着率", "2連対率", "3連対率", "平均ST"]]
    kim = [t for t in parser.tables if t and t[0][:4] == ["コース", "1着", "逃げ", "差し"]]
    if len(stats) < 2 or len(kim) < 2:
        raise ValueError(f"recent tables not found at {url}")
    range_match = re.search(r"直近6か月（(?:<!-- -->)?(\d{4}/\d{2}/\d{2})(?:<!-- -->)?〜(?:<!-- -->)?(\d{4}/\d{2}/\d{2})", html)
    period = f"{range_match.group(1)}..{range_match.group(2)}" if range_match else "unknown"
    return stats[1], kim[1], period


def compare_kyoteidb(con: sqlite3.Connection, racers: Sequence[str]) -> dict[str, object]:
    local = {(str(r["reg_no"]), int(r["actual_course"])): r for r in export_rows(con)}
    details: list[dict[str, object]] = []
    for reg_no in racers:
        stats, kim, period = fetch_kyoteidb_course(reg_no)
        kim_by_course = {int(row[0][0]): row for row in kim[1:]}
        for remote in stats[1:]:
            course = int(remote[0][0])
            ours = local.get((reg_no, course))
            remote_kim = kim_by_course.get(course, [])
            expected = {
                "starts": int(remote[1].replace(",", "")),
                "rates": [float(remote[index].rstrip("%")) for index in (3, 4, 5)],
                "average_st": None if remote[6] == "-" else float(remote[6]),
                "finish_counts": [int(remote[index].replace(",", "")) for index in range(7, 13)],
                "kimarite": [int(v.replace(",", "")) for v in remote_kim[2:8]] if remote_kim else [],
            }
            def remote_display_rate(value: object) -> float | None:
                if value is None:
                    return None
                # The public table renders one-decimal truncation with two
                # trailing digits (e.g. 7/22 => 31.80%).
                return math.floor(float(value) * 10) / 10
            actual = {
                "starts": int(ours["starts"]) if ours else None,
                "rates": [remote_display_rate(ours[name]) for name in ("win_rate", "top2_rate", "top3_rate")] if ours else [],
                "average_st": ours["average_st"] if ours else None,
                "finish_counts": [int(ours[f"finish{rank}"]) for rank in range(1, 7)] if ours else [],
                "kimarite": [int(ours[name]) for name in (
                    "kimarite_escape", "kimarite_sashi", "kimarite_makuri",
                    "kimarite_makurizashi", "kimarite_nuki", "kimarite_megumi",
                )] if ours else [],
            }
            matched = expected == actual
            details.append({"reg_no": reg_no, "course": course, "period": period,
                            "matched": matched, "expected": expected, "actual": actual})
    return {"checked": len(details), "matched": sum(bool(x["matched"]) for x in details), "details": details}


def command_backfill(args: argparse.Namespace) -> int:
    start, end = parse_iso_date(args.start), parse_iso_date(args.end)
    started = time.perf_counter()
    failures: list[dict[str, str]] = []
    with connect_db(Path(args.db)) as con:
        for index, day in enumerate(iter_dates(start, end), 1):
            if not args.refresh and con.execute("SELECT 1 FROM source_days WHERE race_date=? AND status='ok'", (day.isoformat(),)).fetchone():
                continue
            try:
                count = download_and_ingest(con, day, timeout=args.timeout, retries=args.retries)
                if index % 20 == 0 or day == end:
                    print(f"[{index}] {day}: {count} rows", flush=True)
            except Exception as exc:  # retain other days and report a bounded failure list
                failures.append({"date": day.isoformat(), "error": str(exc)})
                print(f"ERROR {day}: {exc}", file=sys.stderr, flush=True)
        latest_row = con.execute("SELECT MAX(race_date) FROM source_days WHERE status='ok'").fetchone()
        if latest_row and latest_row[0]:
            # A narrow repair must restore the current cache, not move its
            # as-of date backwards to the repaired historical day.
            rebuild_rolling(con, parse_iso_date(latest_row[0]))
        exported = write_export(con, Path(args.export))
        missing = missing_days(con, start, end)
        summary = {
            "command": "backfill", "started_at": jst_now(), "source_start": start.isoformat(),
            "requested_end": end.isoformat(), "latest_acquired": latest_row[0] if latest_row else None,
            "missing_days": missing, "missing_count": len(missing), "failures": failures,
            "duplicate_keys": duplicate_count(con), "export_rows": exported,
            "elapsed_seconds": round(time.perf_counter() - started, 3), "success": not failures and not missing,
        }
    write_status(Path(args.status), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["success"] else 1


def command_daily(args: argparse.Namespace) -> int:
    target = parse_iso_date(args.date) if args.date else datetime.now(JST).date() - timedelta(days=1)
    started = time.perf_counter()
    status: dict[str, object] = {"command": "daily", "target_date": target.isoformat(), "started_at": jst_now()}
    try:
        with connect_db(Path(args.db)) as con:
            old = list(con.execute("SELECT * FROM daily_course_stats WHERE race_date=?", (target.isoformat(),)))
            count = download_and_ingest(con, target, timeout=args.timeout, retries=args.retries)
            differential_refresh(con, target, replaced_day_old=old or None)
            exported = write_export(con, Path(args.export))
            latest = con.execute("SELECT MAX(race_date) FROM source_days WHERE status='ok'").fetchone()[0]
            window_start = rolling_window_start(target)
            missing = missing_days(con, window_start, target)
            status.update(success=True, rows_ingested=count, export_rows=exported, latest_acquired=latest,
                          window_start=window_start.isoformat(), missing_days=missing, missing_count=len(missing),
                          duplicate_keys=duplicate_count(con))
    except Exception as exc:
        status.update(success=False, retained_previous_cache=True, error=str(exc))
    status["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    write_status(Path(args.status), status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status["success"] or args.soft_fail:
        return 0
    return 1


def command_verify(args: argparse.Namespace) -> int:
    with connect_db(Path(args.db)) as con:
        minmax = con.execute("SELECT MIN(race_date),MAX(race_date),COUNT(*) FROM source_days WHERE status='ok'").fetchone()
        start = parse_iso_date(args.start) if args.start else parse_iso_date(minmax[0])
        end = parse_iso_date(args.end) if args.end else parse_iso_date(minmax[1])
        report: dict[str, object] = {
            "source_min": minmax[0], "source_max": minmax[1], "source_days": minmax[2],
            "missing_days": missing_days(con, start, end), "duplicate_keys": duplicate_count(con),
            "result_rows": con.execute("SELECT COUNT(*) FROM race_results").fetchone()[0],
            "races": con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT race_date,venue,race_no FROM race_results)").fetchone()[0],
            "rolling_groups": con.execute("SELECT COUNT(*) FROM rolling_course_stats").fetchone()[0],
            "finish_counts": dict(con.execute("SELECT finish_code,COUNT(*) FROM race_results GROUP BY finish_code")),
        }
        if args.compare:
            report["kyoteidb"] = compare_kyoteidb(con, [x.strip() for x in args.compare.split(",") if x.strip()])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["missing_days"] or report["duplicate_keys"]:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--export", default=str(DEFAULT_EXPORT))
    parser.add_argument("--status", default=str(DEFAULT_STATUS))
    sub = parser.add_subparsers(dest="command", required=True)
    backfill = sub.add_parser("backfill")
    backfill.add_argument("--start", required=True); backfill.add_argument("--end", required=True)
    backfill.add_argument("--refresh", action="store_true"); backfill.add_argument("--timeout", type=float, default=20)
    backfill.add_argument("--retries", type=int, default=2); backfill.set_defaults(func=command_backfill)
    daily = sub.add_parser("daily")
    daily.add_argument("--date"); daily.add_argument("--timeout", type=float, default=20)
    daily.add_argument("--retries", type=int, default=2); daily.add_argument("--soft-fail", action="store_true")
    daily.set_defaults(func=command_daily)
    verify = sub.add_parser("verify")
    verify.add_argument("--start"); verify.add_argument("--end"); verify.add_argument("--compare")
    verify.set_defaults(func=command_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
