import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "automation" / "build_site_data.py"
SPEC = importlib.util.spec_from_file_location("build_site_data_race_type", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def entry_lines(race_type: str, *, entry_fixed: bool = False) -> list[str]:
    lines = [
        "下関",
        "8月16日 (日) 最終日",
        "一般",
        "ＭＮＢＲ下関４ｔｈふく〜る下関オープン１４周年記念",
        *(f"{race_no}R" for race_no in range(1, 13)),
        "締切 20:56",
        race_type,
    ]
    if entry_fixed:
        lines.extend(["8Rは", "進入固定", "のレースです"])
    else:
        lines.append("1800m")
    lines.extend(["出走表", "連対率・展開"])
    return lines


class RaceTypeParserTests(unittest.TestCase):
    def test_shimonoseki_examples(self):
        self.assertEqual(MODULE.parse_race_type(entry_lines("一般")), "一般")
        self.assertEqual(MODULE.parse_race_type(entry_lines("一般", entry_fixed=True)), "一般")
        self.assertEqual(MODULE.parse_race_type(entry_lines("シーモ特選")), "シーモ特選")

    def test_entry_fixed_comes_from_current_entry_header(self):
        self.assertTrue(MODULE.parse_entry_fixed(entry_lines("一般", entry_fixed=True)))
        self.assertFalse(MODULE.parse_entry_fixed(entry_lines("一般")))
        self.assertFalse(MODULE.parse_entry_fixed(entry_lines("シーモ特選")))

    def test_entry_fixed_ignores_text_after_entry_header(self):
        lines = entry_lines("一般") + ["進入固定の説明"]
        self.assertFalse(MODULE.parse_entry_fixed(lines))

    def test_common_logic_preserves_all_boaters_labels(self):
        for label in ("優勝戦", "準優勝戦", "選抜戦", "特選", "特別選抜戦", "地域限定タイトル"):
            with self.subTest(label=label):
                self.assertEqual(MODULE.parse_race_type(entry_lines(label)), label)

    def test_missing_type_is_rejected(self):
        self.assertEqual(MODULE.parse_race_type(["締切 17:41"]), "")

    def test_build_payload_writes_all_twelve_types_without_dropping_fields(self):
        labels = [
            "一般", "一般", "一般", "一般", "長州ファイブ", "一般",
            "ふく〜る戦", "一般", "シーモ特選", "特別選抜戦", "準優勝戦", "優勝戦",
        ]
        racers = [{"lane": lane, "name": f"選手{lane}"} for lane in range(1, 7)]
        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory)
            races_dir = source_dir / "races"
            races_dir.mkdir()
            for race_no, label in enumerate(labels, 1):
                lines = entry_lines(label, entry_fixed=race_no == 8)
                lines[4 + 12] = f"締切 {17 + race_no // 3:02d}:{race_no * 4 % 60:02d}"
                (races_dir / f"race_{race_no:02d}_entry.txt").write_text(
                    "\n".join(lines), encoding="utf-8"
                )

            with (
                patch.object(MODULE, "parse_racers", return_value=racers),
                patch.object(MODULE, "event_day_info", return_value=(6, "最終日")),
            ):
                payload, detail = MODULE.build_payload(
                    {"slug": "shimonoseki", "name": "下関"}, "2026-08-16", source_dir
                )

        self.assertEqual(detail, {"reason": "ok"})
        self.assertIsNotNone(payload)
        races = payload["races"]
        self.assertEqual(len(races), 12)
        self.assertEqual([race["type"] for race in races], labels)
        self.assertEqual([race["entryFixed"] for race in races], [race == 8 for race in range(1, 13)])
        self.assertTrue(all(len(race["racers"]) == 6 for race in races))
        self.assertTrue(
            all(
                {"race", "deadline", "title", "type", "entryFixed", "racers", "entry_changes", "eventDayLabel", "eventDay"}
                <= race.keys()
                for race in races
            )
        )

    def test_build_payload_attaches_only_explicit_boaters_local_st(self):
        with tempfile.TemporaryDirectory() as directory:
            source_dir = Path(directory)
            races_dir = source_dir / "races"
            races_dir.mkdir()
            for race_no in range(1, 13):
                (races_dir / f"race_{race_no:02d}_entry.txt").write_text(
                    "\n".join(entry_lines("一般")), encoding="utf-8"
                )
            (races_dir / "race_01_boaters_local_st.json").write_text(
                json.dumps(
                    {
                        "racers": [
                            {
                                "lane": lane,
                                "boaters_local_avg_st": f"0.{16 + lane:02d}",
                                "boaters_local_st_rank": f"{lane}.0位",
                            }
                            for lane in range(1, 7)
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(
                    MODULE,
                    "parse_racers",
                    side_effect=lambda _lines: [
                        {"lane": lane, "name": f"選手{lane}", "avg_st": "0.99"}
                        for lane in range(1, 7)
                    ],
                ),
                patch.object(MODULE, "event_day_info", return_value=(1, "初日")),
            ):
                payload, detail = MODULE.build_payload(
                    {"slug": "ashiya", "name": "芦屋"}, "2026-08-24", source_dir
                )

        self.assertEqual(detail, {"reason": "ok"})
        first = payload["races"][0]["racers"]
        self.assertEqual(
            [racer.get("boaters_local_avg_st") for racer in first],
            ["0.17", "0.18", "0.19", "0.20", "0.21", "0.22"],
        )
        self.assertEqual(first[0]["avg_st"], "0.99")
        self.assertTrue(
            all("boaters_local_avg_st" not in racer for racer in payload["races"][1]["racers"])
        )


if __name__ == "__main__":
    unittest.main()
