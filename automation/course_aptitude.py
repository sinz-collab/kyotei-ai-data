#!/usr/bin/env python3
"""Attach official six-month course results and fixed aptitude labels to payloads."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import statistics
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "player_course_6m" / "player_course_6m.sqlite"
DEFAULT_CLASS_CACHE = ROOT / "data" / "player_course_6m" / "racer_classes.json"
FAN_URL = (
    "https://www.boatrace.jp/static_extra/pc_static/download/data/"
    "kibetsu/fan{period}.lzh"
)
MIN_STARTS = 10
MIN_PEERS = 10
WEIGHTS = (0.3, 0.3, 0.4)
RATE_KEYS = ("win_rate", "top2_rate", "top3_rate")
KIMARITE_KEYS = {
    "逃げ": "kimarite_escape",
    "差し": "kimarite_sashi",
    "まくり": "kimarite_makuri",
    "まくり差し": "kimarite_makurizashi",
    "抜き": "kimarite_nuki",
    "恵まれ": "kimarite_megumi",
}


def normalize_name(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def class_period_code(evaluation_date: date) -> str:
    """Return the official fan-book period effective on the evaluation date."""
    if evaluation_date.month <= 6:
        return f"{(evaluation_date.year - 1) % 100:02d}10"
    return f"{evaluation_date.year % 100:02d}04"


def parse_fan_classes(raw: bytes) -> dict[str, str]:
    """Parse registration number and current grade from official fixed-width FAN data."""
    classes: dict[str, str] = {}
    for line in raw.splitlines():
        if len(line) < 41:
            continue
        reg_no = line[0:4].decode("ascii", errors="ignore").strip()
        grade = line[39:41].decode("ascii", errors="ignore").strip().upper()
        if re.fullmatch(r"\d{4}", reg_no) and grade in {"A1", "A2", "B1", "B2"}:
            classes[reg_no] = grade
    return classes


def _read_lzh(path: Path) -> bytes:
    try:
        import lhafile  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency is installed in production
        raise RuntimeError("lhafile_dependency_missing") from exc
    archive = lhafile.Lhafile(str(path))
    names = archive.namelist()
    if len(names) != 1:
        raise ValueError(f"unexpected_fan_archive_members:{names}")
    return archive.read(names[0])


def refresh_class_cache(
    evaluation_date: date,
    cache_path: Path = DEFAULT_CLASS_CACHE,
    timeout: float = 20.0,
) -> dict[str, Any]:
    period = class_period_code(evaluation_date)
    url = FAN_URL.format(period=period)
    request = urllib.request.Request(url, headers={"User-Agent": "kyotei-ai-data/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        archive_bytes = response.read()
    with tempfile.TemporaryDirectory(prefix="fan-grade-") as temporary:
        archive_path = Path(temporary) / f"fan{period}.lzh"
        archive_path.write_bytes(archive_bytes)
        classes = parse_fan_classes(_read_lzh(archive_path))
    if len(classes) < 1000:
        raise ValueError(f"official_class_master_too_small:{len(classes)}")
    payload = {
        "period": period,
        "evaluation_year": evaluation_date.year,
        "source": url,
        "racer_count": len(classes),
        "classes": classes,
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(cache_path)
    return payload


def load_class_cache(
    evaluation_date: date,
    cache_path: Path = DEFAULT_CLASS_CACHE,
    refresh: bool = True,
) -> tuple[dict[str, str], str | None]:
    expected_period = class_period_code(evaluation_date)
    cached: dict[str, Any] = {}
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            cached = {}
    if cached.get("period") == expected_period and isinstance(cached.get("classes"), dict):
        return {str(k): str(v) for k, v in cached["classes"].items()}, None
    if refresh:
        try:
            fresh = refresh_class_cache(evaluation_date, cache_path)
            return {str(k): str(v) for k, v in fresh["classes"].items()}, None
        except Exception as exc:
            return {}, f"class_master_unavailable:{exc}"
    return {}, "class_master_unavailable"


@dataclass(frozen=True)
class CourseRow:
    reg_no: str
    racer_name: str
    course: int
    starts: int
    win_rate: float
    top2_rate: float
    top3_rate: float
    average_st: float | None
    kimarite: dict[str, int]


def _rate(count: int, starts: int) -> float:
    return count / starts


def load_course_rows(db_path: Path = DEFAULT_DB) -> tuple[list[CourseRow], dict[str, str]]:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        metadata = dict(con.execute("SELECT key,value FROM metadata"))
        rows: list[CourseRow] = []
        for item in con.execute("SELECT * FROM rolling_course_stats"):
            starts = int(item["starts"])
            if starts <= 0:
                continue
            st_count = int(item["st_count"])
            average_st = (
                round(int(item["st_sum_millis"]) / st_count / 1000, 2)
                if st_count
                else None
            )
            rows.append(
                CourseRow(
                    reg_no=str(item["reg_no"]),
                    racer_name=str(item["racer_name"]),
                    course=int(item["actual_course"]),
                    starts=starts,
                    win_rate=_rate(int(item["win"]), starts),
                    top2_rate=_rate(int(item["top2"]), starts),
                    top3_rate=_rate(int(item["top3"]), starts),
                    average_st=average_st,
                    kimarite={
                        label: int(item[column])
                        for label, column in KIMARITE_KEYS.items()
                    },
                )
            )
        return rows, metadata
    finally:
        con.close()


def unavailable_record(
    reason: str,
    *,
    reg_no: str = "",
    course: int | None = None,
    starts: int | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "status": "unavailable",
        "aptitude_label": "判定不可",
        "reason": reason,
        "registration_no": reg_no,
        "course": course,
        "starts": starts,
        "source": "official_race_results",
        "period_months": 6,
    }
    if metadata:
        record["as_of"] = metadata.get("rolling_as_of")
        record["window_start"] = metadata.get("rolling_window_start")
    return record


class CourseAptitudeEvaluator:
    def __init__(
        self,
        rows: Iterable[CourseRow],
        classes: dict[str, str],
        metadata: dict[str, str] | None = None,
        class_error: str | None = None,
    ) -> None:
        self.rows = list(rows)
        self.classes = classes
        self.metadata = metadata or {}
        self.class_error = class_error
        self.by_key = {(row.reg_no, row.course): row for row in self.rows}
        names: dict[str, set[str]] = {}
        for row in self.rows:
            names.setdefault(normalize_name(row.racer_name), set()).add(row.reg_no)
        self.by_name = {
            name: next(iter(registrations))
            for name, registrations in names.items()
            if name and len(registrations) == 1
        }
        self.peer_groups: dict[tuple[str, int], list[CourseRow]] = {}
        for row in self.rows:
            grade = self.classes.get(row.reg_no)
            if grade:
                self.peer_groups.setdefault((grade, row.course), []).append(row)

    @classmethod
    def from_files(
        cls,
        evaluation_date: date,
        db_path: Path = DEFAULT_DB,
        class_cache: Path = DEFAULT_CLASS_CACHE,
        refresh_classes: bool = True,
    ) -> "CourseAptitudeEvaluator":
        rows, metadata = load_course_rows(db_path)
        classes, class_error = load_class_cache(
            evaluation_date, class_cache, refresh=refresh_classes
        )
        return cls(rows, classes, metadata, class_error)

    def registration_no(self, racer: dict[str, Any]) -> str:
        direct = str(racer.get("player_id") or racer.get("reg_no") or "").strip()
        if re.fullmatch(r"\d{4}", direct):
            return direct
        return self.by_name.get(normalize_name(racer.get("name")), "")

    @staticmethod
    def actual_course(racer: dict[str, Any]) -> int | None:
        raw = (
            racer.get("actual_course")
            or racer.get("entry_course")
            or racer.get("course")
            or racer.get("lane")
        )
        try:
            course = int(raw)
        except (TypeError, ValueError):
            return None
        return course if course in range(1, 7) else None

    def evaluate(self, racer: dict[str, Any]) -> dict[str, Any]:
        reg_no = self.registration_no(racer)
        course = self.actual_course(racer)
        if not reg_no or course is None:
            return unavailable_record(
                "identity_or_course_missing", reg_no=reg_no, course=course, metadata=self.metadata
            )
        subject = self.by_key.get((reg_no, course))
        if subject is None:
            return unavailable_record(
                "course_result_missing", reg_no=reg_no, course=course, metadata=self.metadata
            )
        base = {
            "registration_no": reg_no,
            "course": course,
            "starts": subject.starts,
            "win_rate": subject.win_rate,
            "top2_rate": subject.top2_rate,
            "top3_rate": subject.top3_rate,
            "average_st": subject.average_st,
            "kimarite": subject.kimarite,
        }
        if subject.starts < MIN_STARTS:
            return {**unavailable_record("starts_under_10", metadata=self.metadata), **base}
        if subject.average_st is None or any(
            not math.isfinite(getattr(subject, key)) for key in RATE_KEYS
        ):
            return {**unavailable_record("required_value_missing", metadata=self.metadata), **base}
        if self.class_error:
            return {**unavailable_record(self.class_error, metadata=self.metadata), **base}
        grade = self.classes.get(reg_no) or str(racer.get("class") or "").upper()
        if grade not in {"A1", "A2", "B1", "B2"}:
            return {**unavailable_record("evaluation_class_missing", metadata=self.metadata), **base}
        peers = [
            row
            for row in self.peer_groups.get((grade, course), [])
            if row.reg_no != reg_no
        ]
        if len(peers) < MIN_PEERS:
            return {
                **unavailable_record("comparison_group_under_10", metadata=self.metadata),
                **base,
                "evaluation_class": grade,
                "comparison_size": len(peers),
            }
        means = [statistics.fmean(getattr(row, key) for row in peers) for key in RATE_KEYS]
        deviations = [statistics.pstdev(getattr(row, key) for row in peers) for key in RATE_KEYS]
        if any(not math.isfinite(value) or value == 0 for value in deviations):
            return {
                **unavailable_record("standard_deviation_zero", metadata=self.metadata),
                **base,
                "evaluation_class": grade,
                "comparison_size": len(peers),
            }
        z_scores = [
            (getattr(subject, key) - mean) / deviation
            for key, mean, deviation in zip(RATE_KEYS, means, deviations)
        ]
        weighted_z = sum(weight * z for weight, z in zip(WEIGHTS, z_scores))
        raw_score = 50 + 10 * weighted_z * subject.starts / (subject.starts + 20)
        label = "得意" if raw_score >= 60 else "標準" if raw_score >= 40 else "苦手"
        return {
            "status": "available",
            "aptitude_label": label,
            "aptitude_score": min(100.0, max(0.0, raw_score)),
            "aptitude_score_raw": raw_score,
            "evaluation_class": grade,
            "comparison_size": len(peers),
            "comparison_mean": dict(zip(RATE_KEYS, means)),
            "comparison_stddev": dict(zip(RATE_KEYS, deviations)),
            "z_scores": dict(zip(RATE_KEYS, z_scores)),
            "source": "official_race_results",
            "period_months": 6,
            "as_of": self.metadata.get("rolling_as_of"),
            "window_start": self.metadata.get("rolling_window_start"),
            **base,
        }

    def enrich_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        available = 0
        unavailable = 0
        for race in payload.get("races") or []:
            collections: list[tuple[str, list[dict[str, Any]]]] = []
            for key in ("racers", "entries"):
                rows = race.get(key)
                if isinstance(rows, list):
                    collections.append((key, rows))
            primary_key = "racers" if isinstance(race.get("racers"), list) else "entries"
            seen: set[int] = set()
            for collection_key, racers in collections:
                for racer in racers:
                    if not isinstance(racer, dict) or id(racer) in seen:
                        continue
                    seen.add(id(racer))
                    by_course = {
                        str(course): self.public_record(
                            self.evaluate({**racer, "actual_course": course})
                        )
                        for course in range(1, 7)
                    }
                    record = by_course.get(str(self.actual_course(racer))) or unavailable_record(
                        "identity_or_course_missing", metadata=self.metadata
                    )
                    if collection_key == primary_key:
                        racer["recent_course_6m_by_course"] = by_course
                    else:
                        racer.pop("recent_course_6m_by_course", None)
                    racer["recent_course_6m"] = record
                    if collection_key == primary_key:
                        if record["status"] == "available":
                            available += 1
                        else:
                            unavailable += 1
        payload.setdefault("sourceStatus", {})["course_6m"] = {
            "status": "loaded" if available else "unavailable",
            "source": "official_race_results",
            "as_of": self.metadata.get("rolling_as_of"),
            "window_start": self.metadata.get("rolling_window_start"),
            "available": available,
            "unavailable": unavailable,
            "class_error": self.class_error,
        }
        return {"available": available, "unavailable": unavailable}

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        """Keep payloads compact while retaining every field used by the UI."""
        keys = (
            "status", "aptitude_label", "aptitude_score", "reason",
            "registration_no", "course", "starts", "win_rate", "top2_rate",
            "top3_rate", "average_st", "kimarite", "evaluation_class",
            "comparison_size", "comparison_mean", "source", "period_months",
            "as_of", "window_start",
        )
        return {key: record[key] for key in keys if key in record}
