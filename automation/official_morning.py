#!/usr/bin/env python3
"""Build isolated morning candidates from BOAT RACE official race cards.

This module is deliberately not connected to the production publisher.  It
updates only the morning-entry domain of a copied, already validated payload;
predictions, history, live, odds and results remain byte-for-byte equivalent
as JSON values.  The command writes candidates and a validation report below
``work/`` (or another explicit output directory).
"""

from __future__ import annotations

import argparse
import copy
import http.cookiejar
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from bs4 import BeautifulSoup
from enrich_all_venues_kimarite import parse_race_kimarite
from enrich_motor_recent import parse_motor_recent_text
from validate_morning_vps_artifacts import payload_error as existing_payload_error


OFFICIAL_BASE = "https://www.boatrace.jp/owpc/pc/race/racelist"
VENUES: dict[str, tuple[str, str]] = {
    "kiryu": ("01", "桐生"),
    "toda": ("02", "戸田"),
    "heiwajima": ("04", "平和島"),
    "tokoname": ("08", "常滑"),
    "biwako": ("11", "びわこ"),
    "naruto": ("14", "鳴門"),
    "shimonoseki": ("19", "下関"),
    "wakamatsu": ("20", "若松"),
    "ashiya": ("21", "芦屋"),
    "fukuoka": ("22", "福岡"),
    "karatsu": ("23", "唐津"),
    "omura": ("24", "大村"),
}

CORE_RACER_FIELDS = (
    "lane", "player_id", "name", "class", "age", "weight", "branch",
    "f", "l", "avg_st", "nat_win", "nat_2", "nat_3", "local_win",
    "local_2", "local_3", "motor_no", "motor_2", "motor_3", "boat_no",
    "boat_2", "boat_3", "season_runs", "season_groups",
)
REQUIRED_RACER_FIELDS = set(CORE_RACER_FIELDS) - {"season_runs", "season_groups"}
PROTECTED_RACE_KEYS = {
    "prediction", "predictionPre", "live", "odds", "result",
    "motor_recent", "motorRecentAvailable",
}
PROTECTED_TOP_KEYS = {"preds", "predictionStatus", "predictionReason"}
SUPPLEMENT_KEYS = {
    "local_st", "boaters_local_avg_st", "boaters_local_st_rank",
    "motor_recent", "recent_course_6m", "recent_course_6m_by_course",
}
SUPPLEMENT_PREFIXES = ("boaters_",)
SPECIAL_FINISHES = {"転", "妨", "落", "失", "不", "F", "L", "欠", "エ", "沈"}


class NoMeetingError(ValueError):
    """The requested venue/date has no official race-card tables."""


def normalize_text(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def normalize_name(value: object) -> str:
    return re.sub(r"[\s\u3000]+", "", normalize_text(value))


def official_url(slug: str, target_date: str, race_no: int) -> str:
    if slug not in VENUES:
        raise ValueError(f"unsupported venue: {slug}")
    datetime.strptime(target_date, "%Y-%m-%d")
    if race_no not in range(1, 13):
        raise ValueError(f"race out of range: {race_no}")
    query = urllib.parse.urlencode(
        {"rno": race_no, "jcd": VENUES[slug][0], "hd": target_date.replace("-", "")}
    )
    return f"{OFFICIAL_BASE}?{query}"


def one_decimal(value: object) -> str:
    number = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{number:.1f}"


def average_st(value: object) -> str:
    text = normalize_text(value)
    if re.fullmatch(r"0\.\d{2}", text):
        return text[1:]
    return text


def cell_text(cell: Any) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def finish_value(value: object) -> str:
    text = normalize_text(value)
    if re.fullmatch(r"[1-6]", text):
        return f"{text}着"
    if text in SPECIAL_FINISHES:
        return text
    return text


def event_day_from_page(soup: BeautifulSoup, target_date: str) -> tuple[int | None, str | None]:
    target = datetime.strptime(target_date, "%Y-%m-%d")
    text = normalize_text(soup.get_text(" ", strip=True))
    match = re.search(
        rf"{target.month}月\s*{target.day}日(?:\s*\([^)]*\))?\s*(初日|[0-9]+日目|最終日)",
        text,
    )
    if not match:
        return None, None
    label = match.group(1)
    if label == "初日":
        return 1, label
    numbered = re.fullmatch(r"([0-9]+)日目", label)
    if numbered:
        return int(numbered.group(1)), label
    schedule = re.findall(r"\d+月\s*\d+日(?:\s*\([^)]*\))?\s*(初日|[0-9]+日目|最終日)", text)
    return (schedule.index("最終日") + 1 if "最終日" in schedule else None), label


def parse_identity(value: str) -> dict[str, Any]:
    match = re.match(
        r"(\d{4})\s*/\s*([AB][12])\s+(.+?)\s+([^/\s]+)/([^\s]+)\s+(\d+)歳/([0-9.]+)kg$",
        normalize_text(value),
    )
    if not match:
        raise ValueError(f"official identity format changed: {value!r}")
    return {
        "player_id": int(match.group(1)),
        "class": match.group(2),
        "name": " ".join(match.group(3).split()),
        "branch": match.group(4),
        "birthplace": match.group(5),
        "age": match.group(6),
        "weight": match.group(7),
    }


def parse_official_html(
    html: bytes | str,
    *,
    slug: str,
    target_date: str,
    race_no: int,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Parse one official PC race-card page into the existing morning schema."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    entry_table = next(
        (
            table for table in tables
            if "ボートレーサー" in cell_text(table)
            and "モーター" in cell_text(table)
            and "当地" in cell_text(table)
        ),
        None,
    )
    deadline_table = next((table for table in tables if "締切予定時刻" in cell_text(table)), None)
    if entry_table is None and deadline_table is None:
        raise NoMeetingError("official race-card tables not found")
    if entry_table is None or deadline_table is None:
        raise ValueError("official race-card page incomplete")

    deadline_values = re.findall(r"\b[0-9]{1,2}:[0-9]{2}\b", cell_text(deadline_table))
    if len(deadline_values) != 12:
        raise ValueError(f"official deadline count invalid: {len(deadline_values)}")

    rows = entry_table.find_all("tr", recursive=True)
    base_indexes = []
    for index, row in enumerate(rows):
        cells = row.find_all("td", recursive=False)
        classes = cells[0].get("class", []) if cells else []
        if cells and cells[0].get("rowspan") == "4" and any("is-boatColor" in item for item in classes):
            base_indexes.append(index)
    if len(base_indexes) != 6:
        raise ValueError(f"official racer count invalid: {len(base_indexes)}")

    day_header = next(
        (
            row for row in rows
            if "初日" in cell_text(row)
            and any(header.get("colspan") == "2" for header in row.find_all("th", recursive=False))
        ),
        None,
    )
    if day_header is None:
        raise ValueError("official meeting-day header missing")
    day_labels = [
        normalize_text(header.get_text(" ", strip=True))
        for header in day_header.find_all("th", recursive=False)
        if header.get("colspan") == "2" and normalize_text(header.get_text(" ", strip=True))
    ][:6]
    racers = []
    for lane, base_index in enumerate(base_indexes, 1):
        group = rows[base_index:base_index + 4]
        if len(group) != 4:
            raise ValueError(f"official racer row group incomplete: lane={lane}")
        cells = group[0].find_all("td", recursive=False)
        if len(cells) < 23:
            raise ValueError(f"official racer columns invalid: lane={lane} columns={len(cells)}")
        identity = parse_identity(cell_text(cells[2]))
        flst = cell_text(cells[3]).split()
        national = cell_text(cells[4]).split()
        local = cell_text(cells[5]).split()
        motor = cell_text(cells[6]).split()
        boat = cell_text(cells[7]).split()
        if not all(len(values) == 3 for values in (flst, national, local, motor, boat)):
            raise ValueError(f"official numeric block invalid: lane={lane}")

        history_rows = [[cell_text(item) for item in cells[9:-1]]]
        history_rows.extend(
            [cell_text(item) for item in row.find_all("td", recursive=False)]
            for row in group[1:]
        )
        width = min(map(len, history_rows))
        runs: list[dict[str, str]] = []
        grouped: dict[int, list[dict[str, str]]] = {}
        for column in range(width):
            race_value = normalize_text(history_rows[0][column])
            if not race_value:
                continue
            if re.fullmatch(r"\d{1,2}", race_value):
                race_value += "R"
            run = {
                "race": race_value,
                "course": normalize_text(history_rows[1][column]),
                "entry_course": normalize_text(history_rows[1][column]),
                "st": average_st(history_rows[2][column]),
                "finish": finish_value(history_rows[3][column]),
            }
            if not re.fullmatch(r"\d{1,2}R", run["race"]):
                raise ValueError(f"official meeting race invalid: lane={lane} value={run['race']}")
            course_missing_for_accident = (
                not run["course"] and run["finish"] in SPECIAL_FINISHES
            )
            if not re.fullmatch(r"[1-6]", run["course"]) and not course_missing_for_accident:
                raise ValueError(f"official meeting course invalid: lane={lane} value={run['course']}")
            runs.append(run)
            grouped.setdefault(column // 2, []).append(copy.deepcopy(run))

        season_groups = [
            {
                "day": day_labels[day] if day < len(day_labels) else f"{day + 1}日目",
                "runs": grouped[day],
            }
            for day in sorted(grouped)
        ]
        racer = {
            "lane": lane,
            **identity,
            "f": flst[0].removeprefix("F"),
            "l": flst[1].removeprefix("L"),
            "avg_st": average_st(flst[2]),
            "nat_win": national[0],
            "nat_2": one_decimal(national[1]),
            "nat_3": one_decimal(national[2]),
            "local_win": local[0],
            "local_2": one_decimal(local[1]),
            "local_3": one_decimal(local[2]),
            "motor_no": motor[0],
            "motor_2": one_decimal(motor[1]),
            "motor_3": one_decimal(motor[2]),
            "boat_no": boat[0],
            "boat_2": one_decimal(boat[1]),
            "boat_3": one_decimal(boat[2]),
            "season_runs": runs,
            "season_groups": season_groups,
        }
        hayami = cell_text(cells[-1])
        if hayami:
            racer["hayami"] = hayami
        racers.append(racer)

    heading_node = next(
        (item for item in soup.find_all("h3") if "1800m" in normalize_text(item.get_text(" ", strip=True))),
        None,
    )
    heading = normalize_text(heading_node.get_text(" ", strip=True)) if heading_node else ""
    race_type = re.sub(r"\s*1800m.*$", "", heading).strip()
    if not race_type:
        raise ValueError("official race type missing")
    event_title_node = soup.find("h2")
    event_day, event_day_label = event_day_from_page(soup, target_date)
    return {
        "race": race_no,
        "deadline": deadline_values[race_no - 1],
        "title": VENUES[slug][1],
        "type": race_type,
        "entryFixed": "進入固定" in heading,
        "racers": racers,
        "eventDay": event_day,
        "eventDayLabel": event_day_label,
        "officialEventTitle": normalize_text(event_title_node.get_text(" ", strip=True)) if event_title_node else "",
        "source": source_url or official_url(slug, target_date, race_no),
    }


@dataclass
class FetchResult:
    url: str
    status: int
    elapsed_seconds: float
    bytes_received: int
    html: bytes


class OfficialClient:
    """Single-cookie-session client with a fixed minimum request interval."""

    def __init__(self, min_interval: float = 1.0, timeout: float = 25.0) -> None:
        if min_interval < 0.5:
            raise ValueError("min_interval must be at least 0.5 seconds")
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_started = 0.0
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def get(self, url: str) -> FetchResult:
        remaining = self.min_interval - (time.monotonic() - self._last_started)
        if remaining > 0:
            time.sleep(remaining)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "kyotei-ai-data-official-validation/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        self._last_started = time.monotonic()
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read()
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            # Do not retry or work around access controls (especially 403/429).
            raise RuntimeError(f"official_http_error:{exc.code}:{url}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"official_transport_error:{exc.reason}:{url}") from exc
        return FetchResult(
            url=url,
            status=status,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            bytes_received=len(content),
            html=content,
        )


def racer_identity(racer: dict[str, Any]) -> tuple[str, str]:
    return str(racer.get("player_id") or ""), normalize_name(racer.get("name"))


def protected_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection = {key: copy.deepcopy(payload.get(key)) for key in PROTECTED_TOP_KEYS if key in payload}
    projection["races"] = []
    for race in payload.get("races") or []:
        item = {key: copy.deepcopy(race.get(key)) for key in PROTECTED_RACE_KEYS if key in race}
        item["racers"] = []
        for racer in race.get("racers") or []:
            item["racers"].append(
                {
                    key: copy.deepcopy(value)
                    for key, value in racer.items()
                    if key in SUPPLEMENT_KEYS or key.startswith(SUPPLEMENT_PREFIXES)
                }
            )
        projection["races"].append(item)
    return projection


def merge_official_races(
    baseline: dict[str, Any],
    official_races: Iterable[dict[str, Any]],
    *,
    slug: str,
    target_date: str,
) -> dict[str, Any]:
    if baseline.get("venueId") != slug or baseline.get("date") != target_date:
        raise ValueError("baseline identity mismatch")
    candidate = copy.deepcopy(baseline)
    by_race = {int(item.get("race") or 0): item for item in candidate.get("races") or []}
    merged_races = []
    for official in official_races:
        race_no = int(official["race"])
        target = by_race.get(race_no)
        if target is None:
            raise ValueError(f"baseline race missing: {race_no}")
        existing_racers = target.get("racers") or []
        by_player = {racer_identity(item): item for item in existing_racers}
        if len(existing_racers) != 6 or len(by_player) != 6:
            raise ValueError(f"baseline racers invalid: race={race_no}")
        for official_racer in official["racers"]:
            identity = racer_identity(official_racer)
            current = by_player.get(identity)
            if current is None:
                raise ValueError(
                    f"official/baseline racer mismatch: race={race_no} lane={official_racer.get('lane')} identity={identity}"
                )
            for key in CORE_RACER_FIELDS:
                current[key] = copy.deepcopy(official_racer[key])
            if official_racer.get("hayami"):
                current["hayami"] = official_racer["hayami"]
        # Public payloads expose the same engine input under both racers and
        # entries.  Keep those views aligned without touching live course data.
        entries = target.get("entries")
        if isinstance(entries, list):
            entries_by_player = {racer_identity(item): item for item in entries}
            if len(entries) != 6 or len(entries_by_player) != 6:
                raise ValueError(f"baseline entries invalid: race={race_no}")
            for official_racer in official["racers"]:
                entry = entries_by_player.get(racer_identity(official_racer))
                if entry is None:
                    raise ValueError(f"official/baseline entry mismatch: race={race_no}")
                for key in CORE_RACER_FIELDS:
                    entry[key] = copy.deepcopy(official_racer[key])
                if official_racer.get("hayami"):
                    entry["hayami"] = official_racer["hayami"]
        setsukan = target.get("setsukan")
        if isinstance(setsukan, list):
            setsukan_by_lane = {int(item.get("lane") or 0): item for item in setsukan}
            if sorted(setsukan_by_lane) != list(range(1, 7)):
                raise ValueError(f"baseline setsukan invalid: race={race_no}")
            for official_racer in official["racers"]:
                item = setsukan_by_lane[int(official_racer["lane"])]
                item["season_runs"] = copy.deepcopy(official_racer["season_runs"])
                item["season_groups"] = copy.deepcopy(official_racer["season_groups"])
        for key in ("deadline", "title", "type", "entryFixed"):
            target[key] = copy.deepcopy(official[key])
        if isinstance(target.get("race_meta"), dict):
            target["race_meta"]["deadline"] = official["deadline"]
        if official.get("eventDay"):
            target["eventDay"] = official["eventDay"]
            target["eventDayLabel"] = official.get("eventDayLabel")
            candidate["eventDay"] = official["eventDay"]
            candidate["eventDayLabel"] = official.get("eventDayLabel")
            candidate["seriesDay"] = official.get("eventDayLabel")
        if official.get("officialEventTitle"):
            candidate["officialEventTitle"] = official["officialEventTitle"]
        merged_races.append(race_no)
    candidate.setdefault("sourceStatus", {})["official_morning"] = {
        "status": "validated_candidate",
        "source": "boatrace_official_racelist",
        "date": target_date,
        "races": sorted(merged_races),
        "production_publish_enabled": False,
    }
    return candidate


def validate_candidate(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    requested_races: Iterable[int],
) -> list[str]:
    errors: list[str] = []
    if baseline is not None and protected_projection(baseline) != protected_projection(candidate):
        errors.append("protected_prediction_live_or_supplement_domain_changed")
    by_race = {int(item.get("race") or 0): item for item in candidate.get("races") or []}
    for race_no in requested_races:
        race = by_race.get(int(race_no))
        if not race:
            errors.append(f"race_{race_no:02d}_missing")
            continue
        racers = race.get("racers") or []
        if len(racers) != 6:
            errors.append(f"race_{race_no:02d}_racer_count:{len(racers)}")
            continue
        if sorted(int(item.get("lane") or 0) for item in racers) != list(range(1, 7)):
            errors.append(f"race_{race_no:02d}_lanes_invalid")
        if not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}", str(race.get("deadline") or "")):
            errors.append(f"race_{race_no:02d}_deadline_invalid")
        if not str(race.get("type") or "").strip():
            errors.append(f"race_{race_no:02d}_type_missing")
        for racer in racers:
            missing = [
                key for key in sorted(REQUIRED_RACER_FIELDS)
                if key not in racer or racer[key] in (None, "")
            ]
            if missing:
                errors.append(
                    f"race_{race_no:02d}_lane_{racer.get('lane')}_missing:{','.join(missing)}"
                )
            if not isinstance(racer.get("season_runs"), list):
                errors.append(
                    f"race_{race_no:02d}_lane_{racer.get('lane')}_season_runs_invalid"
                )
            if not isinstance(racer.get("season_groups"), list):
                errors.append(
                    f"race_{race_no:02d}_lane_{racer.get('lane')}_season_groups_invalid"
                )
            for run in racer.get("season_runs") or []:
                if not all(key in run for key in ("race", "course", "st", "finish")):
                    errors.append(
                        f"race_{race_no:02d}_lane_{racer.get('lane')}_season_run_incomplete"
                    )
            supplement_groups = {
                "motor_recent": ("motor_recent",),
                "kimarite": ("boaters_kimarite_starts",),
                "recent_course_6m": ("recent_course_6m",),
            }
            local_available = any(
                re.fullmatch(r"0?\.\d{2}", str(racer.get(key) or ""))
                for key in ("local_st", "boaters_local_avg_st")
            )
            local_source_missing = (
                racer.get("boaters_local_st_status") == "source_missing"
                and racer.get("boaters_local_st_missing_reason") == "source_dash"
            )
            if not local_available and not local_source_missing:
                errors.append(
                    f"race_{race_no:02d}_lane_{racer.get('lane')}_supplement_missing:local_st"
                )
            if not racer.get("boaters_local_st_rank") and not local_source_missing:
                errors.append(
                    f"race_{race_no:02d}_lane_{racer.get('lane')}_supplement_missing:local_st_rank"
                )
            for label, keys in supplement_groups.items():
                if not any(key in racer for key in keys):
                    errors.append(
                        f"race_{race_no:02d}_lane_{racer.get('lane')}_supplement_missing:{label}"
                    )
        entries = race.get("entries")
        if isinstance(entries, list):
            racer_by_identity = {racer_identity(item): item for item in racers}
            if len(entries) != 6:
                errors.append(f"race_{race_no:02d}_entry_count:{len(entries)}")
            for entry in entries:
                racer = racer_by_identity.get(racer_identity(entry))
                if racer is None:
                    errors.append(f"race_{race_no:02d}_entry_identity_mismatch")
                    continue
                if any(entry.get(key) != racer.get(key) for key in CORE_RACER_FIELDS):
                    errors.append(f"race_{race_no:02d}_lane_{entry.get('lane')}_entry_core_mismatch")
        setsukan = race.get("setsukan")
        if isinstance(setsukan, list):
            racers_by_lane = {int(item.get("lane") or 0): item for item in racers}
            if sorted(int(item.get("lane") or 0) for item in setsukan) != list(range(1, 7)):
                errors.append(f"race_{race_no:02d}_setsukan_lanes_invalid")
            for item in setsukan:
                racer = racers_by_lane.get(int(item.get("lane") or 0), {})
                if (
                    item.get("season_runs") != racer.get("season_runs")
                    or item.get("season_groups") != racer.get("season_groups")
                ):
                    errors.append(f"race_{race_no:02d}_lane_{item.get('lane')}_setsukan_mismatch")
    try:
        json.dumps(candidate, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        errors.append(f"json_serialization:{exc}")
    return errors


def compare_core(baseline: dict[str, Any], official: Iterable[dict[str, Any]]) -> dict[str, Any]:
    baseline_races = {int(item.get("race") or 0): item for item in baseline.get("races") or []}
    fields = {key: {"equal": 0, "total": 0, "mismatches": []} for key in CORE_RACER_FIELDS if key not in {"season_groups"}}
    for official_race in official:
        race_no = int(official_race["race"])
        base = baseline_races[race_no]
        existing = {racer_identity(item): item for item in base.get("racers") or []}
        for observed in official_race["racers"]:
            current = existing.get(racer_identity(observed), {})
            for key, stat in fields.items():
                stat["total"] += 1
                if current.get(key) == observed.get(key):
                    stat["equal"] += 1
                elif len(stat["mismatches"]) < 10:
                    stat["mismatches"].append(
                        {
                            "race": race_no,
                            "lane": observed.get("lane"),
                            "official": observed.get(key),
                            "existing": current.get(key),
                        }
                    )
    for stat in fields.values():
        stat["rate"] = round(stat["equal"] * 100.0 / stat["total"], 2) if stat["total"] else 0.0
    return fields


def atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_csv(value: str, *, allowed: set[str] | None = None) -> list[str]:
    values = [item.strip() for item in value.split(",") if item.strip()]
    if not values:
        raise ValueError("empty selection")
    if len(values) != len(set(values)):
        raise ValueError("duplicate selection")
    if allowed is not None and any(item not in allowed for item in values):
        raise ValueError(f"invalid selection: {values}")
    return values


def cached_race_path(cache_root: Path, target_date: str, slug: str, race_no: int) -> Path:
    return cache_root / target_date.replace("-", "") / slug / f"{race_no:02d}.json"


def validate_official_race(race: dict[str, Any], race_no: int) -> None:
    if int(race.get("race") or 0) != race_no:
        raise ValueError("cached race number mismatch")
    racers = race.get("racers") or []
    if len(racers) != 6:
        raise ValueError(f"official racer count invalid: {len(racers)}")
    if sorted(int(item.get("lane") or 0) for item in racers) != list(range(1, 7)):
        raise ValueError("official lanes invalid")
    for racer in racers:
        missing = [
            key for key in sorted(REQUIRED_RACER_FIELDS)
            if key not in racer or racer[key] in (None, "")
        ]
        if missing:
            raise ValueError(f"official racer fields missing: {','.join(missing)}")
        if not isinstance(racer.get("season_runs"), list) or not isinstance(
            racer.get("season_groups"), list
        ):
            raise ValueError("official meeting history invalid")


def load_cached_race(
    cache_root: Path, target_date: str, slug: str, race_no: int
) -> dict[str, Any] | None:
    path = cached_race_path(cache_root, target_date, slug, race_no)
    if not path.is_file():
        return None
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        expected_url = official_url(slug, target_date, race_no)
        if (
            envelope.get("schemaVersion") != 1
            or envelope.get("date") != target_date
            or envelope.get("venue") != slug
            or envelope.get("url") != expected_url
        ):
            return None
        race = envelope["parsed"]
        validate_official_race(race, race_no)
        return race
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_cached_race(
    cache_root: Path,
    target_date: str,
    slug: str,
    race_no: int,
    race: dict[str, Any],
) -> None:
    validate_official_race(race, race_no)
    atomic_json(
        cached_race_path(cache_root, target_date, slug, race_no),
        {
            "schemaVersion": 1,
            "date": target_date,
            "venue": slug,
            "race": race_no,
            "url": official_url(slug, target_date, race_no),
            "parsed": race,
        },
    )


def history_files(baseline_root: Path, target_date: str) -> list[tuple[str, Path]]:
    """Return recent immutable public payloads, newest first, for supplementation."""
    cutoff = target_date.replace("-", "")
    found: list[tuple[str, Path]] = []
    for slug in VENUES:
        venue_root = baseline_root / slug
        if not venue_root.is_dir():
            continue
        dated = [
            path for path in venue_root.glob("????????.json")
            if path.stem.isdigit() and path.stem <= cutoff
        ]
        # Six months covers the course master horizon while bounding Actions I/O.
        for path in sorted(dated, key=lambda item: item.stem, reverse=True)[:180]:
            found.append((slug, path))
    return sorted(found, key=lambda item: item[1].stem, reverse=True)


def build_supplement_indexes(
    baseline_root: Path, target_date: str
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[tuple[str, str, int], dict[str, Any]],
]:
    local_by_name: dict[tuple[str, str], dict[str, Any]] = {}
    motor_by_number: dict[tuple[str, str], dict[str, Any]] = {}
    course_by_name: dict[str, dict[str, Any]] = {}
    kimarite_by_name_lane: dict[tuple[str, str, int], dict[str, Any]] = {}
    for slug, path in history_files(baseline_root, target_date):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for race in payload.get("races") or []:
            for racer in race.get("racers") or []:
                indexed = copy.deepcopy(racer)
                indexed["_supplement_meta"] = {
                    "source": "existing_public_history",
                    "source_path": str(path),
                    "fetched_at": _source_time(path),
                    "missing_reason": "",
                }
                name = normalize_name(racer.get("name"))
                lane = int(racer.get("lane") or 0)
                motor_no = str(racer.get("motor_no") or "")
                if not name:
                    continue
                local_by_name.setdefault((slug, name), indexed)
                if motor_no:
                    motor_by_number.setdefault((slug, motor_no), indexed)
                existing_course = course_by_name.get(name)
                if "recent_course_6m_by_course" in racer and (
                    existing_course is None
                    or "recent_course_6m_by_course" not in existing_course
                ):
                    course_by_name[name] = indexed
                elif "recent_course_6m" in racer and existing_course is None:
                    course_by_name[name] = indexed
                if lane in range(1, 7):
                    kimarite_by_name_lane.setdefault((slug, name, lane), indexed)
                    kimarite_by_name_lane.setdefault(("*", name, lane), indexed)
    return local_by_name, motor_by_number, course_by_name, kimarite_by_name_lane


def supplement_official_racer(
    racer: dict[str, Any],
    *,
    slug: str,
    local_by_name: dict[tuple[str, str], dict[str, Any]],
    motor_by_number: dict[tuple[str, str], dict[str, Any]],
    course_by_name: dict[str, dict[str, Any]],
    kimarite_by_name_lane: dict[tuple[str, str, int], dict[str, Any]],
) -> dict[str, Any]:
    enriched = copy.deepcopy(racer)
    enriched["actual_course"] = enriched["lane"]
    enriched["entry_course"] = enriched["lane"]
    name = normalize_name(enriched.get("name"))
    lane = int(enriched["lane"])
    local_source = local_by_name.get((slug, name), {})
    motor_source = motor_by_number.get((slug, str(enriched.get("motor_no") or "")), {})
    course_source = course_by_name.get(name, {})
    kimarite_source = kimarite_by_name_lane.get((slug, name, lane)) or kimarite_by_name_lane.get(
        ("*", name, lane), {}
    )
    provenance = enriched.setdefault("supplement_sources", {})
    for key in ("local_st", "boaters_local_avg_st", "boaters_local_st_rank"):
        if key in local_source:
            enriched[key] = copy.deepcopy(local_source[key])
    if any(key in local_source for key in ("local_st", "boaters_local_avg_st")):
        provenance["local_st"] = copy.deepcopy(local_source.get("_supplement_meta", {}))
    if "motor_recent" in motor_source:
        enriched["motor_recent"] = copy.deepcopy(motor_source["motor_recent"])
        enriched["motor_recent"]["lane"] = lane
        enriched["motor_recent"]["motor_no"] = str(enriched["motor_no"])
        provenance["motor_recent"] = copy.deepcopy(motor_source.get("_supplement_meta", {}))
    for key, value in kimarite_source.items():
        if key.startswith("boaters_") and key not in {
            "boaters_local_avg_st", "boaters_local_st_rank"
        }:
            enriched[key] = copy.deepcopy(value)
    if "boaters_kimarite_starts" in kimarite_source:
        provenance["kimarite"] = copy.deepcopy(kimarite_source.get("_supplement_meta", {}))
    by_course = course_source.get("recent_course_6m_by_course")
    if isinstance(by_course, dict):
        enriched["recent_course_6m_by_course"] = copy.deepcopy(by_course)
        selected = by_course.get(str(lane)) or by_course.get(lane)
        if selected:
            enriched["recent_course_6m"] = copy.deepcopy(selected)
            provenance["recent_course_6m"] = copy.deepcopy(course_source.get("_supplement_meta", {}))
    elif isinstance(course_source.get("recent_course_6m"), dict):
        selected = course_source["recent_course_6m"]
        if selected.get("course") in (None, "", lane, str(lane)):
            enriched["recent_course_6m"] = copy.deepcopy(selected)
            provenance["recent_course_6m"] = copy.deepcopy(course_source.get("_supplement_meta", {}))
    enriched["officialSupplementSource"] = "existing_public_history"
    return enriched


def _source_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds")


def _boaters_race_dir(
    boaters_root: Path, slug: str, target_date: str, race_no: int
) -> Path | None:
    marker = f"race_{race_no:02d}_boaters_local_st.json"
    date_token = target_date.replace("-", "")
    venue_name = VENUES[slug][1]
    candidates = [
        path.parent
        for path in boaters_root.rglob(marker)
        if date_token in str(path) and (slug in path.parts or venue_name in path.parts)
    ]
    return max(candidates, key=lambda item: (item / marker).stat().st_mtime) if candidates else None


def apply_boaters_cache(
    candidate: dict[str, Any], *, boaters_root: Path, slug: str, target_date: str
) -> dict[str, Any]:
    """Apply current-day BOATERS cache without performing network access."""
    stats = {"races": 0, "localAvailable": 0, "localSourceMissing": 0,
             "motorRecent": 0, "kimarite": 0, "matchFallbacks": 0}
    for race in candidate.get("races") or []:
        race_no = int(race.get("race") or 0)
        race_dir = _boaters_race_dir(boaters_root, slug, target_date, race_no)
        if race_dir is None:
            continue
        stats["races"] += 1
        racers = race.get("racers") or []
        local_path = race_dir / f"race_{race_no:02d}_boaters_local_st.json"
        motor_path = race_dir / f"race_{race_no:02d}_motor.txt"
        data_path = race_dir / f"race_{race_no:02d}_data.txt"
        local_payload: dict[str, Any] = {}
        if local_path.is_file():
            try:
                local_payload = json.loads(local_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                local_payload = {}
        local_rows = local_payload.get("racers") or []
        by_id = {
            str(row.get("player_id") or "").strip(): row
            for row in local_rows if str(row.get("player_id") or "").strip()
        }
        by_lane = {int(row.get("lane") or 0): row for row in local_rows}
        motors = parse_motor_recent_text(motor_path) if motor_path.is_file() else {}
        kimarite = (
            parse_race_kimarite(data_path.read_text(encoding="utf-8", errors="replace"), racers)
            if data_path.is_file() else {}
        )
        for racer in racers:
            lane = int(racer.get("lane") or 0)
            player_id = str(racer.get("player_id") or "").strip()
            local = by_id.get(player_id)
            match_method = "registration_no"
            if local is None:
                fallback = by_lane.get(lane)
                if fallback and normalize_name(fallback.get("name")) == normalize_name(racer.get("name")):
                    local = fallback
                    match_method = "lane_and_name"
                    stats["matchFallbacks"] += 1
            provenance = racer.setdefault("supplement_sources", {})
            if local is not None:
                for key in (
                    "boaters_local_avg_st", "boaters_local_st_rank",
                    "boaters_local_st_status", "boaters_local_st_missing_reason",
                    "boaters_local_st_source_value", "boaters_local_st_rank_source_value",
                ):
                    if key in local:
                        racer[key] = copy.deepcopy(local[key])
                source_missing = (
                    local.get("boaters_local_st_status") == "source_missing"
                    or local.get("boaters_local_st_source_value") == "-"
                    or (
                        local.get("boaters_local_avg_st") in (None, "")
                        and local.get("boaters_local_st_rank") in (None, "")
                    )
                )
                if source_missing:
                    racer["local_st"] = ""
                    racer["boaters_local_avg_st"] = ""
                    racer["boaters_local_st_status"] = "source_missing"
                    racer["boaters_local_st_missing_reason"] = "source_dash"
                    stats["localSourceMissing"] += 1
                elif racer.get("boaters_local_avg_st"):
                    racer["boaters_local_st_status"] = "available"
                    stats["localAvailable"] += 1
                provenance["local_st"] = {
                    "source": local_payload.get("source") or "BOATERS local start ranking",
                    "source_url": local_payload.get("source_url") or "",
                    "fetched_at": local_payload.get("fetched_at") or _source_time(local_path),
                    "match": match_method,
                    "missing_reason": racer.get("boaters_local_st_missing_reason") or "",
                }
            motor = motors.get(lane)
            if motor and str(motor.get("motor_no") or "") == str(racer.get("motor_no") or ""):
                racer["motor_recent"] = copy.deepcopy(motor)
                provenance["motor_recent"] = {
                    "source": "BOATERS motor recent 10",
                    "fetched_at": _source_time(motor_path),
                    "match": "motor_no_and_lane",
                    "missing_reason": "",
                }
                stats["motorRecent"] += 1
            row = kimarite.get(lane)
            if row:
                racer.update(copy.deepcopy(row))
                provenance["kimarite"] = {
                    "source": "BOATERS six-month race data",
                    "fetched_at": _source_time(data_path),
                    "match": "registration_no_and_lane" if player_id else "lane_and_name",
                    "missing_reason": "",
                }
                stats["kimarite"] += 1
        if isinstance(race.get("entries"), list):
            race["entries"] = copy.deepcopy(racers)
    return stats


def supplement_coverage(candidate: dict[str, Any]) -> dict[str, Any]:
    racers = [
        racer
        for race in candidate.get("races") or []
        for racer in race.get("racers") or []
    ]
    total = len(racers)
    source_missing = sum(
        racer.get("boaters_local_st_status") == "source_missing"
        and racer.get("boaters_local_st_missing_reason") == "source_dash"
        for racer in racers
    )
    available = {
        "localAvgSt": sum(
            bool(re.fullmatch(r"0?\.\d{2}", str(racer.get("boaters_local_avg_st") or racer.get("local_st") or "")))
            for racer in racers
        ),
        "localStRank": sum(bool(racer.get("boaters_local_st_rank")) for racer in racers),
        "motorRecent": sum(isinstance(racer.get("motor_recent"), dict) for racer in racers),
        "kimarite": sum("boaters_kimarite_starts" in racer for racer in racers),
    }
    return {
        "targetRacers": total,
        "available": available,
        "validSourceMissing": {"localAvgSt": source_missing, "localStRank": source_missing},
        "missing": {
            key: total - count - (source_missing if key in {"localAvgSt", "localStRank"} else 0)
            for key, count in available.items()
        },
    }


def build_candidate_from_official(
    official_races: list[dict[str, Any]],
    *,
    slug: str,
    target_date: str,
    supplement_indexes: tuple[
        dict[tuple[str, str], dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[tuple[str, str, int], dict[str, Any]],
    ],
) -> dict[str, Any]:
    local_by_name, motor_by_number, course_by_name, kimarite_by_name_lane = supplement_indexes
    races: list[dict[str, Any]] = []
    for official in sorted(official_races, key=lambda item: int(item["race"])):
        racers = [
            supplement_official_racer(
                racer,
                slug=slug,
                local_by_name=local_by_name,
                motor_by_number=motor_by_number,
                course_by_name=course_by_name,
                kimarite_by_name_lane=kimarite_by_name_lane,
            )
            for racer in official["racers"]
        ]
        races.append(
            {
                "race": official["race"],
                "deadline": official["deadline"],
                "title": official["title"],
                "type": official["type"],
                "entryFixed": official["entryFixed"],
                "eventDay": official.get("eventDay"),
                "eventDayLabel": official.get("eventDayLabel"),
                "racers": racers,
                "entries": copy.deepcopy(racers),
                "setsukan": [
                    {
                        "lane": racer["lane"],
                        "season_runs": copy.deepcopy(racer["season_runs"]),
                        "season_groups": copy.deepcopy(racer["season_groups"]),
                    }
                    for racer in racers
                ],
                "source": official["source"],
            }
        )
    first = official_races[0]
    return {
        "venueId": slug,
        "venue": VENUES[slug][1],
        "date": target_date,
        "eventDay": first.get("eventDay"),
        "eventDayLabel": first.get("eventDayLabel"),
        "seriesDay": first.get("eventDayLabel"),
        "officialEventTitle": first.get("officialEventTitle"),
        "races": races,
        "sourceStatus": {
            "official_morning": {
                "status": "validated_candidate",
                "source": "boatrace_official_racelist",
                "supplements": "existing_public_history",
                "date": target_date,
                "races": [int(item["race"]) for item in official_races],
                "production_publish_enabled": False,
            }
        },
    }


def acquire_race(
    client: OfficialClient,
    *,
    cache_root: Path,
    target_date: str,
    slug: str,
    race_no: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    url = official_url(slug, target_date, race_no)
    cached = load_cached_race(cache_root, target_date, slug, race_no)
    if cached is not None:
        return cached, {
            "race": race_no,
            "url": url,
            "source": "cache",
            "status": 200,
            "elapsedSeconds": 0.0,
            "bytes": cached_race_path(cache_root, target_date, slug, race_no).stat().st_size,
        }
    fetched = client.get(url)
    parsed = parse_official_html(
        fetched.html,
        slug=slug,
        target_date=target_date,
        race_no=race_no,
        source_url=url,
    )
    validate_official_race(parsed, race_no)
    save_cached_race(cache_root, target_date, slug, race_no, parsed)
    return parsed, {
        "race": race_no,
        "url": url,
        "source": "network",
        "status": fetched.status,
        "elapsedSeconds": fetched.elapsed_seconds,
        "bytes": fetched.bytes_received,
    }


def run_validation(
    *,
    target_date: str,
    venues: list[str],
    race_numbers: list[int],
    baseline_root: Path,
    output_root: Path,
    cache_root: Path,
    client: OfficialClient,
    boaters_root: Path | None = None,
) -> dict[str, Any]:
    """Detect held venues, acquire requested races, and build isolated candidates."""
    started = time.perf_counter()
    report: dict[str, Any] = {
        "date": target_date,
        "mode": "manual_validation_only",
        "productionPublishEnabled": False,
        "requestedVenues": venues,
        "requestedRaces": race_numbers,
        "venues": {},
        "errors": [],
    }
    date_compact = target_date.replace("-", "")
    held: list[str] = []
    first_races: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    # A valid official 1R card is the held/not-held signal. The fetched card is
    # retained and reused below, so detection does not add a duplicate request.
    for slug in venues:
        try:
            first_races[slug] = acquire_race(
                client,
                cache_root=cache_root,
                target_date=target_date,
                slug=slug,
                race_no=1,
            )
            held.append(slug)
        except NoMeetingError as exc:
            report["venues"][slug] = {
                "held": False,
                "status": "not_held",
                "officialCoverage": [],
                "errors": [],
                "detection": str(exc),
            }
        except (RuntimeError, ValueError) as exc:
            message = f"{slug}:detection_failed:{exc}"
            report["venues"][slug] = {
                "held": None,
                "status": "detection_failed",
                "officialCoverage": [],
                "errors": [str(exc)],
            }
            report["errors"].append(message)

    supplement_indexes: tuple[
        dict[tuple[str, str], dict[str, Any]],
        dict[tuple[str, str], dict[str, Any]],
        dict[str, dict[str, Any]],
        dict[tuple[str, str, int], dict[str, Any]],
    ] | None = None
    for slug in held:
        baseline_path = baseline_root / slug / f"{date_compact}.json"
        fetches: list[dict[str, Any]] = []
        official_by_race: dict[int, dict[str, Any]] = {}
        race_errors: list[str] = []
        if 1 in race_numbers:
            official_by_race[1], first_fetch = first_races[slug]
            fetches.append(first_fetch)
        for race_no in race_numbers:
            if race_no == 1:
                continue
            try:
                race, fetch = acquire_race(
                    client,
                    cache_root=cache_root,
                    target_date=target_date,
                    slug=slug,
                    race_no=race_no,
                )
                official_by_race[race_no] = race
                fetches.append(fetch)
            except (RuntimeError, ValueError) as exc:
                race_errors.append(f"race_{race_no:02d}:{type(exc).__name__}:{exc}")

        coverage = sorted(official_by_race)
        expected = sorted(race_numbers)
        venue_report: dict[str, Any] = {
            "held": True,
            "status": "failed",
            "officialCoverage": coverage,
            "missingRaces": sorted(set(expected) - set(coverage)),
            "completeOfficialCoverage": coverage == expected,
            "fetches": sorted(fetches, key=lambda item: item["race"]),
            "errors": race_errors,
        }
        if not race_errors and coverage == expected:
            official_races = [official_by_race[number] for number in expected]
            try:
                baseline: dict[str, Any] | None = None
                build_mode = "official_with_historical_supplements"
                if baseline_path.is_file():
                    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
                    candidate = merge_official_races(
                        baseline, official_races, slug=slug, target_date=target_date
                    )
                    build_mode = "existing_same_date_baseline"
                else:
                    if supplement_indexes is None:
                        supplement_indexes = build_supplement_indexes(
                            baseline_root, target_date
                        )
                    candidate = build_candidate_from_official(
                        official_races,
                        slug=slug,
                        target_date=target_date,
                        supplement_indexes=supplement_indexes,
                    )
                venue_report["supplementCoverageBeforeBoatersCache"] = supplement_coverage(candidate)
                if boaters_root is not None and boaters_root.is_dir():
                    venue_report["boatersCache"] = apply_boaters_cache(
                        candidate,
                        boaters_root=boaters_root,
                        slug=slug,
                        target_date=target_date,
                    )
                venue_report["supplementCoverageAfterBoatersCache"] = supplement_coverage(candidate)
                candidate_errors = validate_candidate(
                    baseline, candidate, requested_races=expected
                )
                if len(candidate.get("races") or []) != 12:
                    candidate_errors.append(
                        f"candidate_race_count:{len(candidate.get('races') or [])}"
                    )
                compatibility_error = existing_payload_error(candidate, {"reason": "ok"})
                if compatibility_error:
                    candidate_errors.append(f"engine_input:{compatibility_error}")
                if candidate_errors:
                    venue_report["errors"].extend(candidate_errors)
                else:
                    candidate_path = output_root / "candidates" / slug / f"{date_compact}.json"
                    atomic_json(candidate_path, candidate)
                    venue_report.update(
                        {
                            "status": "ok",
                            "candidate": str(candidate_path),
                            "engineInputCompatible": True,
                            "buildMode": build_mode,
                        }
                    )
                    if baseline is not None:
                        venue_report["comparison"] = compare_core(baseline, official_races)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                venue_report["errors"].append(f"candidate:{type(exc).__name__}:{exc}")
        if venue_report["errors"]:
            report["errors"].extend(f"{slug}:{item}" for item in venue_report["errors"])
        report["venues"][slug] = venue_report

    complete = [slug for slug in held if report["venues"].get(slug, {}).get("status") == "ok"]
    network_seconds = sum(
        fetch.get("elapsedSeconds", 0.0)
        for venue in report["venues"].values()
        for fetch in venue.get("fetches", [])
        if fetch.get("source") == "network"
    )
    report["summary"] = {
        "heldVenueCount": len(held),
        "heldVenues": held,
        "completeVenueCount": len(complete),
        "completeVenues": complete,
        "networkFetchSeconds": round(network_seconds, 3),
        "wallSeconds": round(time.perf_counter() - started, 3),
    }
    report["status"] = "ok" if not report["errors"] and len(complete) == len(held) else "failed"
    atomic_json(output_root / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--venues", default="all")
    parser.add_argument("--races", default="all")
    parser.add_argument("--baseline-root", type=Path, default=Path("data/venues"))
    parser.add_argument("--output-root", type=Path, default=Path("work/official-morning"))
    parser.add_argument("--cache-root", type=Path, default=Path("work/official-morning-cache"))
    parser.add_argument("--boaters-root", type=Path)
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    venues = list(VENUES) if args.venues == "all" else parse_csv(args.venues, allowed=set(VENUES))
    race_numbers = list(range(1, 13)) if args.races == "all" else [int(item) for item in parse_csv(args.races)]
    if any(item not in range(1, 13) for item in race_numbers):
        raise ValueError("race selection must be 1 through 12")

    report = run_validation(
        target_date=args.date,
        venues=venues,
        race_numbers=race_numbers,
        baseline_root=args.baseline_root,
        output_root=args.output_root,
        cache_root=args.cache_root,
        client=OfficialClient(args.min_interval, args.timeout),
        boaters_root=args.boaters_root,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
