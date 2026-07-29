from __future__ import annotations

import unittest

from automation.enrich_all_venues_kimarite import parse_race_kimarite


class AllVenueKimariteTest(unittest.TestCase):
    def test_parse_inner_and_outer_course_rates(self) -> None:
        racers = [
            {"lane": 1, "name": "選手 一郎"},
            {"lane": 2, "name": "選手 二郎"},
            {"lane": 3, "name": "選手 三郎"},
        ]

        text = """
決まり手率

進入変更
直近6ヶ月
直近1年
枠
レーサー
逃げ
差され
まくられ
まくられ差
出走回数
1

選手 一郎

A1

61.2%
（75回）
12.3%
（15回）
9.8%
（12回）
16.7%
（21回）
123回

枠
レーサー
逃し
差し
まくり
まくり差し
出走回数
2

選手 二郎

B1

45.0%
（36回）
20.0%
（16回）
15.0%
（12回）
20.0%
（16回）
80回

3

選手 三郎

B1

-
-
-
-
0回

決まり手率について
AIオッズ評価
"""

        parsed = parse_race_kimarite(text, racers)

        self.assertEqual(parsed[1]["boaters_kimarite_starts"], 123)
        self.assertEqual(parsed[1]["boaters_escape_rate"], 61.2)
        self.assertEqual(parsed[1]["boaters_makurare_zashi_rate"], 16.7)
        self.assertEqual(parsed[2]["boaters_kimarite_starts"], 80)
        self.assertEqual(parsed[2]["boaters_sashi_rate"], 20.0)
        self.assertEqual(parsed[2]["boaters_makuri_sashi_rate"], 20.0)
        self.assertEqual(parsed[3]["boaters_kimarite_starts"], 0)
        self.assertNotIn("boaters_sashi_rate", parsed[3])


if __name__ == "__main__":
    unittest.main()
