from __future__ import annotations

import unittest

from automation.enrich_all_venues_kimarite import (
    parse_race_kimarite,
    parse_recent_course_performance,
    parse_recent_course_st,
    recent_course_record,
)


class AllVenueKimariteTest(unittest.TestCase):
    def test_parse_six_month_course_performance_and_st(self) -> None:
        racers = [
            {"lane": 1, "name": "選手 一郎", "player_id": 4321, "actual_course": 1},
            {"lane": 2, "name": "選手 二郎", "player_id": 5432, "actual_course": 2},
        ]
        text = """
AI3連対率・枠別勝率
進入変更
直近10走
直近6ヶ月
枠 レーサー AI3連対率 3連対率 1着率 2着率 3着率
1
選手 一郎
A1
84.0%
70.0%
(21 / 30回)
30.0%
(9回)
33.3%
(10回)
6.7%
(2回)
2
選手 二郎
B1
20.0%
0.0%
(0 / 0回)
0.0%
(0回)
0.0%
(0回)
0.0%
(0回)
AI3連対率の着順別表示
枠別スタート順位
進入変更
直近6ヶ月
1
選手 一郎
A1
0.12
2.8位
2
選手 二郎
B1
-
-
枠別スタート順位について
"""
        performance = parse_recent_course_performance(text, racers)
        starts = parse_recent_course_st(text, racers)
        self.assertEqual(performance[1]["starts"], 30)
        self.assertEqual(performance[1]["win_rate"], 30.0)
        self.assertEqual(performance[1]["top2_rate"], 63.3)
        self.assertEqual(performance[1]["top3_rate"], 70.0)
        self.assertEqual(starts[1], 0.12)
        available = recent_course_record(racers[0], performance[1], starts[1], None)
        self.assertEqual(available["status"], "available")
        self.assertEqual(available["registration_no"], "4321")
        self.assertEqual(available["course"], 1)
        insufficient = recent_course_record(racers[1], performance[2], None, None)
        self.assertEqual(insufficient["status"], "insufficient")
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
