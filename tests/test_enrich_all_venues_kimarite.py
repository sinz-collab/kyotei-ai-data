from __future__ import annotations

import unittest

from automation.enrich_all_venues_kimarite import parse_race_kimarite


class AllVenueKimariteTest(unittest.TestCase):
    def test_parse_inner_and_outer_course_rates(self) -> None:
        racers = [
            {"lane": 1, "name": "選手 一郎"},
            {"lane": 2, "name": "選手 二郎"},
        ]

        text = """
決まり手率

選手 一郎
逃げ 61.2%
差され 12.3%
まくられ 9.8%
まくられ差し 16.7%
出走回数 123回

選手 二郎
逃し 45.0%
差し 20.0%
まくり 15.0%
まくり差し 20.0%
出走回数 80回

前づけデータ
"""

        parsed = parse_race_kimarite(text, racers)

        self.assertEqual(parsed[1]["boaters_kimarite_starts"], 123)
        self.assertEqual(parsed[1]["boaters_escape_rate"], 61.2)
        self.assertEqual(parsed[1]["boaters_makurare_zashi_rate"], 16.7)
        self.assertEqual(parsed[2]["boaters_kimarite_starts"], 80)
        self.assertEqual(parsed[2]["boaters_sashi_rate"], 20.0)
        self.assertEqual(parsed[2]["boaters_makuri_sashi_rate"], 20.0)


if __name__ == "__main__":
    unittest.main()
