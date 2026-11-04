from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from fetch_original_exhibition import (  # noqa: E402
    merge_kiryu_original_exhibition,
    parse_kiryu_original_exhibition,
)


def official_table(rows: list[tuple[object, object, object]]) -> str:
    return "<table><tbody>" + "".join(
        f'''<tr class="{'odd' if lane % 2 else 'even'}">
          <td class="col1 im_color{lane}" rowspan="2">{lane}</td>
          <td class="col5 rank_1" rowspan="2">{half}</td>
          <td class="col6" rowspan="2">{turn}</td>
          <td class="col7" rowspan="2">{straight}</td>
        </tr>'''
        for lane, (half, turn, straight) in enumerate(rows, 1)
    ) + "</tbody></table>"


class KiryuOriginalExhibitionTests(unittest.TestCase):
    def test_november_4_race_1_official_values_are_half_lap_values(self) -> None:
        parsed = parse_kiryu_original_exhibition(official_table([
            (18.22, 4.54, 7.54),
            (18.46, 4.36, 7.54),
            (18.69, 4.65, 7.58),
            (18.53, 4.82, 7.55),
            (18.70, 4.68, 7.54),
            (18.69, 4.67, 7.68),
        ]))
        self.assertTrue(parsed["_complete"])
        self.assertEqual(
            [row["half_lap_time"] for row in parsed["entries"]],
            [18.22, 18.46, 18.69, 18.53, 18.70, 18.69],
        )
        self.assertTrue(all("lap_time" not in row for row in parsed["entries"]))

    def test_november_3_race_1_measured_values(self) -> None:
        parsed = parse_kiryu_original_exhibition(official_table([
            (18.56, 4.50, 7.69),
            (18.50, 4.53, 7.70),
            (18.42, 4.54, 7.72),
            (18.67, 4.65, 7.64),
            (18.93, 4.66, 7.78),
            (18.39, 4.70, 7.62),
        ]))
        self.assertTrue(parsed["_published"])
        self.assertTrue(parsed["_complete"])
        self.assertEqual(parsed["entries"][5]["half_lap_time"], 18.39)
        self.assertEqual(parsed["entries"][5]["turn_time"], 4.70)
        self.assertEqual(parsed["entries"][5]["straight_time"], 7.62)

    def test_individually_missing_value_stays_null(self) -> None:
        parsed = parse_kiryu_original_exhibition(official_table([
            (18.22, 4.54, 7.54),
            (18.46, 4.36, 7.54),
            (18.69, "—", 7.58),
            (18.53, 4.82, 7.55),
            (18.70, 4.68, 7.54),
            (18.69, 4.67, 7.68),
        ]))
        self.assertTrue(parsed["_published"])
        self.assertFalse(parsed["_complete"])
        self.assertIsNone(parsed["entries"][2]["turn_time"])

    def test_merge_keeps_identity_and_never_maps_half_lap_to_lap_time(self) -> None:
        generic = {
            "entries": [
                {
                    "lane": lane,
                    "player_id": 3000 + lane,
                    "name": f"選手{lane}",
                    "lap_time": None,
                    "turn_time": 4.0,
                    "straight_time": 7.0,
                }
                for lane in range(1, 7)
            ]
        }
        official = parse_kiryu_original_exhibition(official_table([
            (18.1, 4.1, 7.1), (18.2, 4.2, 7.2), (18.3, 4.3, 7.3),
            (18.4, 4.4, 7.4), (18.5, 4.5, 7.5), (18.6, 4.6, 7.6),
        ]))
        merged = merge_kiryu_original_exhibition(generic, official)
        self.assertEqual(merged["metric"], "half_lap")
        self.assertEqual(merged["entries"][0]["player_id"], 3001)
        self.assertNotIn("lap_time", merged["entries"][0])
        self.assertEqual(merged["entries"][0]["half_lap_time"], 18.1)


if __name__ == "__main__":
    unittest.main()
