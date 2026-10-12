from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "engines" / "fukuoka_v1"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from fukuoka_fly_tickets_v14 import tickets_v14  # noqa: E402


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fukuoka_fly_tickets_v14_expectations.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
RESULTS = {
    "20261010": "1-5-4 4-5-2 2-1-3 4-2-5 2-1-3 1-6-5 2-1-3 1-2-3 1-4-3 3-2-6 1-3-6 1-5-2".split(),
    "20261011": "1-3-6 2-1-3 3-5-4 3-4-1 2-1-3 1-2-5 2-1-6 1-4-5 3-6-5 1-2-3 2-3-6 1-4-2".split(),
}
REFERENCE_SHA256 = "F4D7B668D52C722AFDCBC3E0503F2D26ECC606EF00AD9CC25401418C65C5293B"


def data_path(date: str) -> Path:
    return ROOT / "data" / "venues" / "fukuoka" / f"{date}.json"


def committed_blob_sha(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", f"HEAD:{relative}"],
        text=True,
        encoding="utf-8",
    ).strip()


def generated() -> dict[str, dict[str, list[str]]]:
    actual: dict[str, dict[str, list[str]]] = {}
    for date, expected_races in FIXTURE["expected"].items():
        document = json.loads(data_path(date).read_text(encoding="utf-8"))
        actual[date] = {}
        for race_no in expected_races:
            actual[date][race_no] = tickets_v14(
                document["races"][int(race_no) - 1]
            )["tickets"]
    return actual


class TestFukuokaFlyTicketsV14(unittest.TestCase):
    def test_reference_code_is_byte_identical(self):
        reference_path = "engines/fukuoka_v1/fukuoka_fly_tickets_v14.py"
        committed_bytes = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"HEAD:{reference_path}"]
        )
        digest = hashlib.sha256(committed_bytes).hexdigest().upper()
        self.assertEqual(digest, REFERENCE_SHA256)

    def test_source_json_blobs_are_fixed(self):
        for date, expected_sha in FIXTURE["sourceBlobs"].items():
            with self.subTest(date=date):
                path = data_path(date)
                self.assertEqual(committed_blob_sha(path), expected_sha)
                diff = subprocess.run(
                    ["git", "-C", str(ROOT), "diff", "--quiet", "--", str(path)],
                    check=False,
                )
                self.assertEqual(diff.returncode, 0)

    def test_23_standard_entry_races_match_current_expectations(self):
        actual = generated()
        self.assertEqual(sum(map(len, actual.values())), 23)
        for date, races in FIXTURE["expected"].items():
            for race_no, sequence in races.items():
                with self.subTest(date=date, race=race_no):
                    output = actual[date][race_no]
                    self.assertEqual(output, sequence.split())
                    self.assertEqual(len(output), 10)
                    self.assertEqual(len(set(output)), 10)
                    self.assertTrue(all(not ticket.startswith("1-") for ticket in output))

    def test_categories_determinism_and_no_mutation(self):
        for date, races in FIXTURE["expected"].items():
            document = json.loads(data_path(date).read_text(encoding="utf-8"))
            for race_no in races:
                with self.subTest(date=date, race=race_no):
                    race = document["races"][int(race_no) - 1]
                    before = copy.deepcopy(race)
                    first = tickets_v14(race)
                    second = tickets_v14(race)
                    self.assertEqual(first, second)
                    self.assertEqual(race, before)
                    self.assertEqual(first["Main6"], first["tickets"][:6])
                    self.assertEqual(first["Zure2"], first["tickets"][6:8])
                    self.assertEqual(first["Ana2"], first["tickets"][8:10])

    def test_changed_entry_20261010_race6_is_refused(self):
        document = json.loads(data_path("20261010").read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ValueError, "variable_entry_not_supported"):
            tickets_v14(document["races"][5])

    def test_historical_result_metric_is_unchanged(self):
        totals = []
        for date in ("20261010", "20261011"):
            document = json.loads(data_path(date).read_text(encoding="utf-8"))
            hits = 0
            fly_races = 0
            for race, result in zip(document["races"], RESULTS[date]):
                if not result.startswith("1-"):
                    fly_races += 1
                try:
                    output = tickets_v14(race)["tickets"]
                except ValueError:
                    continue
                if result in output:
                    hits += 1
            totals.append((date, hits, fly_races))
        self.assertEqual(totals, [("20261010", 3, 6), ("20261011", 4, 7)])

    def test_fresh_process_reproduces_all_23_races(self):
        script = r'''import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'engines'/'fukuoka_v1'))
from fukuoka_fly_tickets_v14 import tickets_v14
fixture=json.loads((root/'tests'/'fixtures'/'fukuoka_fly_tickets_v14_expectations.json').read_text(encoding='utf-8'))
out={}
for date,races in fixture['expected'].items():
 doc=json.loads((root/'data'/'venues'/'fukuoka'/f'{date}.json').read_text(encoding='utf-8'));out[date]={}
 for race_no in races:out[date][race_no]=tickets_v14(doc['races'][int(race_no)-1])['tickets']
print(hashlib.sha256(json.dumps(out,sort_keys=True,separators=(',',':')).encode()).hexdigest())'''
        expected_digest = hashlib.sha256(
            json.dumps(generated(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        actual_digest = subprocess.check_output(
            [sys.executable, "-c", script, str(ROOT)],
            text=True,
            encoding="utf-8",
        ).strip()
        self.assertEqual(actual_digest, expected_digest)


if __name__ == "__main__":
    unittest.main()
