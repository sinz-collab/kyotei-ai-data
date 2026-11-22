import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "automation"))
from event_labels import event_metadata
from publish_official_stages import update_manifest_entry


class EventLabelTests(unittest.TestCase):
    def test_womens_and_rookie_titles(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(event_metadata({"officialEventTitle": "AL・RB大宮アルディージャWOMENカップ"}, root), ("♥️", "women"))
            self.assertEqual(event_metadata({"officialEventTitle": "ルーキーシリーズ第23戦"}, root), ("🔰", "rookie"))
            self.assertEqual(event_metadata({"officialEventTitle": "G2 モーターボート大賞"}, root), ("G2", "unknown"))
            self.assertEqual(event_metadata({"officialEventTitle": "マスターズリーグ第8戦"}, root), ("G3", "unknown"))
            self.assertEqual(event_metadata({"officialEventTitle": "GⅢマスターズリーグ第8戦"}, root), ("G3", "unknown"))
            self.assertEqual(event_metadata({"officialEventTitle": "開設74周年記念 GⅠびわこ大賞"}, root), ("G1", "unknown"))

    def test_reuses_grade_from_same_meeting_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            previous = root / "venues" / "biwako" / "20261117.json"
            previous.parent.mkdir(parents=True)
            previous.write_text(json.dumps({"eventDay": 4, "eventLabel": "G1", "date": "2026-11-17"}), encoding="utf-8")
            payload = {"venueId": "biwako", "date": "2026-11-19", "eventDay": 6,
                       "officialEventTitle": "開設74周年記念 びわこ大賞"}
            self.assertEqual(event_metadata(payload, root), ("G1", "unknown"))
            payload["eventDay"] = 3
            self.assertEqual(event_metadata(payload, root), ("", "unknown"))

    def test_manifest_receives_meeting_badge(self):
        entry = {}
        payload = {"venueId": "toda", "date": "2026-11-19", "eventLabel": "♥️",
                   "eventType": "women", "eventDay": 2, "eventDayLabel": "2日目",
                   "races": [{"race": i, "deadline": "15:00"} for i in range(1, 13)]}
        update_manifest_entry(entry, payload, "2026-11-19")
        self.assertEqual(entry["eventLabel"], "♥️")
        self.assertEqual(entry["eventType"], "women")


if __name__ == "__main__":
    unittest.main()
