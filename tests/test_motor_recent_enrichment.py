import tempfile
import unittest
from pathlib import Path

from automation.enrich_motor_recent import parse_motor_recent_text


FIXTURE = """モーター直近10走
枠
モーター
No.
前走
2走前
3走前
4走前
5走前
6走前
7走前
8走前
9走前
10走前
平均展示タイム
平均順位
1
No. 71
レーサー
展示タイム/順位
進入 / 着順
選手A
A1
6.80
1位
1
1
着
選手B
B1
6.90
3位
2
2
着
選手C
B1
6.95
5位
3
6
着
選手D
A2
6.85
2位
4
3
着
選手E
B1
7.00
6位
5
Ｆ
選手F
A1
6.88
4位
6
4
着
選手G
B1
6.84
2位
2
2
着
選手H
A2
6.83
2位
3
3
着
選手I
B1
6.89
4位
4
5
着
選手J
A1
6.82
1位
1
1
着
6.88
3.0位
モーター直近10走について
"""


class MotorRecentParserTest(unittest.TestCase):
    def test_parses_recent10_and_special_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "race_01_motor.txt"
            path.write_text(FIXTURE, encoding="utf-8")
            result = parse_motor_recent_text(path)
        self.assertIn(1, result)
        motor = result[1]
        self.assertEqual(motor["motor_no"], "71")
        self.assertEqual(motor["runs_count"], 10)
        self.assertEqual(motor["finishes"][4], "Ｆ")
        self.assertEqual(motor["top2_rate"], 40.0)
        self.assertEqual(motor["top3_rate"], 60.0)
        self.assertEqual(motor["avg_exhibition_time"], 6.88)
        self.assertEqual(motor["avg_exhibition_rank"], 3.0)


if __name__ == "__main__":
    unittest.main()
