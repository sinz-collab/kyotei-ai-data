#!/usr/bin/env python3
"""Official result archive: per-racer, per-actual-course last-3-month ST.

Read-only on the existing six-month SQLite source. Never fabricate a number
from an incomplete source-day window or a one-start sample.
"""

from __future__ import annotations

import calendar
import sqlite3
from datetime import date, timedelta
from pathlib import Path


def subtract_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


class CourseSt3m:
    def __init__(self, db: Path, prediction_date: date, min_starts: int = 2):
        self.date = prediction_date
        self.start = subtract_months(prediction_date, 3)
        self.end = prediction_date - timedelta(days=1)
        self.min_starts = min_starts
        self.by_reg_course: dict[tuple[str, int], tuple[float, int]] = {}
        self.complete = False
        self.reason = "database_missing"
        if not db.is_file():
            return
        with sqlite3.connect(db) as connection:
            # Reject incomplete windows, including a stale but otherwise valid DB.
            try:
                days = {row[0] for row in connection.execute(
                    "SELECT race_date FROM source_days WHERE status='ok' AND race_date BETWEEN ? AND ?",
                    (self.start.isoformat(), self.end.isoformat()),
                )}
                expected = (self.end - self.start).days + 1
                if len(days) != expected:
                    self.reason = f"source_days_incomplete:{len(days)}/{expected}"
                    return
                rows = connection.execute("""
                    SELECT reg_no, actual_course, COUNT(*), AVG(st_millis)
                      FROM race_results
                     WHERE race_date BETWEEN ? AND ?
                       AND race_formed=1 AND actual_course BETWEEN 1 AND 6
                       AND st_millis BETWEEN 0 AND 999
                     GROUP BY reg_no, actual_course
                """, (self.start.isoformat(), self.end.isoformat()))
                for reg_no, course, starts, average_millis in rows:
                    if starts < min_starts:
                        continue
                    self.by_reg_course[(str(reg_no), int(course))] = (
                        round(float(average_millis) / 1000, 2), int(starts)
                    )
            except sqlite3.DatabaseError as exc:
                self.reason = f"database_invalid:{type(exc).__name__}"
                return
        self.complete = True
        self.reason = "ok"

    def select(self, reg_no: str | int, course: int) -> dict:
        value = self.by_reg_course.get((str(reg_no), int(course))) if self.complete else None
        return {
            "status": "available" if value else "unavailable",
            "average_st": value[0] if value else None,
            "starts": value[1] if value else 0,
            "course": int(course),
            "source": "boatrace_official_result_archive",
            "period_months": 3,
            "window_start": self.start.isoformat(),
            "window_end": self.end.isoformat(),
            "reason": "" if value else ("insufficient_course_st" if self.complete else self.reason),
        }

    def course_map(self, reg_no: str | int) -> dict[str, dict]:
        return {str(course): self.select(reg_no, course) for course in range(1, 7)}
