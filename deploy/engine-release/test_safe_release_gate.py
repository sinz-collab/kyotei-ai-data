import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from safe_release_gate import validate_safe_window

JST = ZoneInfo("Asia/Tokyo")


def sample(now):
    venues = [{"slug": f"venue{i}", "open": False} for i in range(24)]
    venues[0] = {
        "slug": "tokoname",
        "open": True,
        "date": now.strftime("%Y-%m-%d"),
        "entryCount": 12,
        "deadlines": [{"race": i, "deadline": f"{10+i//2:02d}:{(i%2)*30:02d}"}
                      for i in range(1, 13)],
    }
    return {
        "date": now.strftime("%Y-%m-%d"),
        "updatedAt": (now - timedelta(minutes=20)).isoformat(),
        "venues": venues,
    }


class SafeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 19, 0, tzinfo=JST)
        self.data = sample(self.now)

    def test_ended_and_quiet(self):
        self.assertEqual(validate_safe_window(self.data, self.now)[0], True)

    def test_upcoming_race_rejected(self):
        self.now = self.now.replace(hour=15, minute=45)
        self.data = sample(self.now)
        self.assertEqual(validate_safe_window(self.data, self.now)[1], "races_not_safely_finished")

    def test_stale_manifest_rejected(self):
        self.data["updatedAt"] = (self.now - timedelta(hours=7)).isoformat()
        self.assertEqual(validate_safe_window(self.data, self.now)[1], "manifest_stale")

    def test_missing_race_rejected(self):
        self.data["venues"][0]["deadlines"].pop()
        self.assertEqual(validate_safe_window(self.data, self.now)[1], "race_deadlines_incomplete")

    def test_missing_open_venues_rejected(self):
        self.data["venues"][0]["open"] = False
        self.assertEqual(validate_safe_window(self.data, self.now)[1], "no_verified_open_venue")

    def test_previous_day_only_before_six(self):
        previous = datetime(2026, 11, 25, 22, 30, tzinfo=JST)
        data = sample(previous)
        data["updatedAt"] = previous.isoformat()
        self.assertTrue(validate_safe_window(data, previous + timedelta(hours=3))[0])
        self.assertEqual(validate_safe_window(data, previous + timedelta(hours=10))[1], "manifest_date_not_current")


if __name__ == "__main__":
    unittest.main()
