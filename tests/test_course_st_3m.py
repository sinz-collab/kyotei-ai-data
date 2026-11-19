import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from automation.course_st_3m import CourseSt3m, subtract_months
from automation.run_official_predictions import apply_official_course_st


class CourseSt3mTests(unittest.TestCase):
    def test_three_month_window_calendar(self):
        self.assertEqual(subtract_months(date(2026, 11, 20), 3), date(2026, 8, 20))
        self.assertEqual(subtract_months(date(2026, 5, 31), 3), date(2026, 2, 28))

    def test_incomplete_days_block_all_predictions(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE source_days(race_date TEXT, status TEXT)")
                con.execute("INSERT INTO source_days VALUES('2026-11-19','ok')")
            model = CourseSt3m(db, date(2026, 11, 20))
            self.assertFalse(model.complete)
            self.assertEqual(model.select("5182", 1)["average_st"], None)

    def test_replaces_only_isolated_engine_st_when_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "test.sqlite"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE source_days(race_date TEXT, status TEXT)")
                con.execute("CREATE TABLE race_results(race_date TEXT, reg_no TEXT, actual_course INTEGER, "
                            "race_formed INTEGER, st_millis INTEGER)")
                from datetime import timedelta
                day = date(2026, 8, 20)
                while day < date(2026, 11, 20):
                    con.execute("INSERT INTO source_days VALUES(?, 'ok')", (day.isoformat(),))
                    day += timedelta(days=1)
                con.executemany("INSERT INTO race_results VALUES('2026-11-01', '5182', 1, 1, ?)",
                                [(110,), (130,)])
            model = CourseSt3m(db, date(2026, 11, 20))
            self.assertTrue(model.complete)
            payload = {"races": [{"racers": [
                {"player_id": 5182, "lane": 1, "local_st": "0.19", "boaters_local_avg_st": "0.22"},
                {"player_id": 5454, "lane": 5, "local_st": "0.14", "boaters_local_avg_st": "0.15"}
            ]}]}
            self.assertEqual(apply_official_course_st(payload, model), 1)
            racers = payload["races"][0]["racers"]
            self.assertEqual(racers[0]["local_st"], "0.12")
            self.assertEqual(racers[0]["boaters_local_avg_st"], "0.12")
            self.assertEqual(racers[1]["local_st"], "")
            self.assertEqual(racers[1]["official_course_st_3m"]["reason"], "insufficient_course_st")

    def test_matches_official_toda_november_20_sample(self):
        # Regression against the six figures supplied from the official screen.
        db = Path("data/player_course_6m/player_course_6m.sqlite")
        if not db.is_file():
            self.skipTest("reference database unavailable")
        model = CourseSt3m(db, date(2026, 11, 20))
        if not model.complete:
            self.skipTest("reference repository DB is older than the race date")
        racers = [("5182", 1, .12), ("4244", 2, .13), ("5200", 3, .18),
                  ("4283", 4, .13), ("5454", 5, None), ("5320", 6, .19)]
        self.assertEqual([model.select(reg, course)["average_st"] for reg, course, _ in racers],
                         [expected for _, _, expected in racers])


if __name__ == "__main__":
    unittest.main()
