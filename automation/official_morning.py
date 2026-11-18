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
    if entry_table is None or deadline_table is None:
        raise ValueError("official race-card tables not found")

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
            if not re.fullmatch(r"[1-6]", run["course"]):
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
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    requested_races: Iterable[int],
) -> list[str]:
    errors: list[str] = []
    if protected_projection(baseline) != protected_projection(candidate):
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
            supplement_groups = {
                "local_st": ("local_st", "boaters_local_avg_st"),
                "motor_recent": ("motor_recent",),
                "kimarite": ("boaters_kimarite_starts",),
                "recent_course_6m": ("recent_course_6m",),
            }
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--venues", default="kiryu,tokoname")
    parser.add_argument("--races", default="1,12")
    parser.add_argument("--baseline-root", type=Path, default=Path("data/venues"))
    parser.add_argument("--output-root", type=Path, default=Path("work/official-morning"))
    parser.add_argument("--min-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    venues = parse_csv(args.venues, allowed=set(VENUES))
    race_numbers = [int(item) for item in parse_csv(args.races)]
    if any(item not in range(1, 13) for item in race_numbers):
        raise ValueError("race selection must be 1 through 12")

    client = OfficialClient(args.min_interval, args.timeout)
    report: dict[str, Any] = {
        "date": args.date,
        "mode": "manual_validation_only",
        "productionPublishEnabled": False,
        "venues": {},
        "errors": [],
    }
    date_compact = args.date.replace("-", "")
    for slug in venues:
        baseline_path = args.baseline_root / slug / f"{date_compact}.json"
        if not baseline_path.is_file():
            report["errors"].append(f"{slug}:baseline_missing:{baseline_path}")
            continue
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        official_races = []
        fetches = []
        try:
            for race_no in race_numbers:
                url = official_url(slug, args.date, race_no)
                fetched = client.get(url)
                official_races.append(
                    parse_official_html(
                        fetched.html,
                        slug=slug,
                        target_date=args.date,
                        race_no=race_no,
                        source_url=url,
                    )
                )
                fetches.append(
                    {
                        "race": race_no,
                        "url": url,
                        "status": fetched.status,
                        "elapsedSeconds": fetched.elapsed_seconds,
                        "bytes": fetched.bytes_received,
                    }
                )
            candidate = merge_official_races(
                baseline, official_races, slug=slug, target_date=args.date
            )
            errors = validate_candidate(
                baseline, candidate, requested_races=race_numbers
            )
            candidate_path = args.output_root / "candidates" / slug / f"{date_compact}.json"
            atomic_json(candidate_path, candidate)
            report["venues"][slug] = {
                "status": "ok" if not errors else "invalid",
                "candidate": str(candidate_path),
                "officialCoverage": race_numbers,
                "completeOfficialCoverage": sorted(race_numbers) == list(range(1, 13)),
                "fetches": fetches,
                "comparison": compare_core(baseline, official_races),
                "errors": errors,
            }
            report["errors"].extend(f"{slug}:{item}" for item in errors)
        except Exception as exc:
            report["venues"][slug] = {
                "status": "failed",
                "fetches": fetches,
                "errors": [f"{type(exc).__name__}:{exc}"],
            }
            report["errors"].append(f"{slug}:{type(exc).__name__}:{exc}")

    report["status"] = "ok" if not report["errors"] else "failed"
    atomic_json(args.output_root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
