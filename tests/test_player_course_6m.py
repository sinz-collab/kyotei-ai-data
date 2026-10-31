from __future__ import annotations

import importlib.util
import sqlite3
import sys
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "automation" / "player_course_6m.py"
SPEC = importlib.util.spec_from_file_location("player_course_6m", MODULE_PATH)
assert SPEC and SPEC.loader
pc = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pc
SPEC.loader.exec_module(pc)


def result(day: str, finish: str, course: int | None, st: int | None, tactic: str | None = None, lane: int = 1):
    return pc.ResultRow(day, "唐津", 1, lane, "4320", "峰竜太", 1, 1, course,
                        finish, None, st, tactic, 1)


def test_window_is_current_month_plus_previous_five():
    assert pc.rolling_window_start(date(2026, 10, 30)) == date(2026, 5, 1)
    assert pc.rolling_window_start(date(2026, 1, 1)) == date(2025, 8, 1)


def test_parser_accepts_three_digit_equipment_number():
    parsed = pc._parse_result_line("  01  1 4424 選手名 28  137  6.72   1    0.15     1.49.9")
    assert parsed is not None
    assert parsed["motor_no"] == 28
    assert parsed["boat_no"] == 137
    assert parsed["actual_course"] == 1


def test_public_counting_rules():
    rows = [
        result("2026-10-01", "1", 1, 120, "逃げ"),
        result("2026-10-01", "F", 1, -10),
        result("2026-10-01", "S0", 1, 180),
        result("2026-10-01", "S1", 1, 190),
        result("2026-10-01", "K0", None, None),
    ]
    daily = pc.build_daily_rows(rows)
    values = dict(zip(("date", "reg", "course", "name", *pc.COUNTER_FIELDS), daily[0]))
    assert values["starts"] == 3  # 1, F, S1; S0 and K0 are excluded
    assert values["win"] == 1
    assert values["st_count"] == 3  # numeric finish plus S0/S1; F excluded
    assert values["st_sum_millis"] == 490
    assert values["kimarite_escape"] == 1


def test_differential_matches_full_rebuild(tmp_path: Path):
    db = tmp_path / "stats.sqlite"
    con = pc.connect_db(db)
    for day, rank in (("2026-09-30", "2"), ("2026-10-01", "1")):
        rows = [result(day, rank, 1, 150, "逃げ" if rank == "1" else None, lane) for lane in range(1, 7)]
        pc.ingest_day(con, date.fromisoformat(day), rows, "fixture", day)
    pc.rebuild_rolling(con, date(2026, 9, 30))
    pc.differential_refresh(con, date(2026, 10, 1))
    diff = tuple(con.execute("SELECT * FROM rolling_course_stats").fetchone())
    pc.rebuild_rolling(con, date(2026, 10, 1))
    full = tuple(con.execute("SELECT * FROM rolling_course_stats").fetchone())
    assert diff == full
    con.close()
