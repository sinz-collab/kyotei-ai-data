from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

import official_morning as official  # noqa: E402
import validate_morning_vps_artifacts as existing_validator  # noqa: E402


class FakeClient:
    def __init__(self, failures: set[int] | None = None) -> None:
        self.failures = failures or set()
        self.calls: list[int] = []

    def get(self, url: str) -> official.FetchResult:
        race_no = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["rno"][0])
        self.calls.append(race_no)
        if race_no in self.failures:
            raise RuntimeError(f"synthetic failure {race_no}")
        body = official_fixture()
        return official.FetchResult(url, 200, 0.01, len(body), body)


def td(value: object = "", **attrs: object) -> str:
    attributes = " ".join(
        f'{"class" if key == "class_" else key.replace("_", "-")}="{value_}"'
        for key, value_ in attrs.items()
    )
    return f"<td {attributes}>{value}</td>"


def official_fixture(*, special_finish: str = "2", missing_lane: int | None = None) -> bytes:
    deadline_rows = "".join(f"<td>{10 + race // 3:02d}:{race * 4 % 60:02d}</td>" for race in range(1, 13))
    entry_rows = [
        "<tr><th rowspan=3>枠</th><th>ボートレーサー</th><th>全国</th><th>当地</th><th>モーター</th></tr>",
        "<tr><th>勝率</th></tr>",
        "<tr>" + "".join(f'<th colspan="2">{label}</th>' for label in ("初日", "2日目", "3日目", "4日目", "5日目", "最終日")) + "</tr>",
    ]
    for lane in range(1, 7):
        if lane == missing_lane:
            continue
        history_race = ["1"] + [""] * 13
        history_course = ["2"] + [""] * 13
        history_st = [".09"] + [""] * 13
        history_finish = [special_finish if lane == 1 else "2"] + [""] * 13
        fixed = [
            td(lane, rowspan=4, class_=f"is-boatColor{lane}"),
            td("", rowspan=4),
            td(f"{4000 + lane} / A1 選手 {lane} 群馬/東京 {30 + lane}歳/52.{lane}kg", rowspan=4),
            td("F0 L0 0.16", rowspan=4),
            td("6.12 45.55 66.65", rowspan=4),
            td("5.80 40.04 60.06", rowspan=4),
            td(f"{20 + lane} 35.05 50.05", rowspan=4),
            td(f"{60 + lane} 30.04 45.05", rowspan=4),
            td("", rowspan=4),
        ]
        entry_rows.append("<tr>" + "".join(fixed + [td(x) for x in history_race] + [td("5R", rowspan=4)]) + "</tr>")
        entry_rows.append("<tr>" + "".join(td(x) for x in history_course) + "</tr>")
        entry_rows.append("<tr>" + "".join(td(x) for x in history_st) + "</tr>")
        entry_rows.append("<tr>" + "".join(td(x) for x in history_finish) + "</tr>")
    return (
        "<html><body><h2>検証競走</h2><div>11月18日2日目</div>"
        f"<table><tr><th>締切予定時刻</th>{deadline_rows}</tr></table>"
        "<h3>予選 1800m</h3><table>" + "".join(entry_rows) + "</table></body></html>"
    ).encode()


def synthetic_payload() -> dict:
    races = []
    for race_no in range(1, 13):
        racers = []
        for lane in range(1, 7):
            racers.append(
                {
                    "lane": lane,
                    "player_id": 4000 + lane,
                    "name": f"選手 {lane}",
                    "class": "A1",
                    "age": str(30 + lane),
                    "weight": f"52.{lane}",
                    "branch": "群馬",
                    "f": "0",
                    "l": "0",
                    "avg_st": ".16",
                    "nat_win": "6.12",
                    "nat_2": "45.6",
                    "nat_3": "66.7",
                    "local_win": "5.80",
                    "local_2": "40.0",
                    "local_3": "60.1",
                    "motor_no": str(20 + lane),
                    "motor_2": "35.1",
                    "motor_3": "50.1",
                    "boat_no": str(60 + lane),
                    "boat_2": "30.0",
                    "boat_3": "45.1",
                    "season_runs": [],
                    "season_groups": [],
                    "actual_course": lane,
                    "entry_course": lane,
                    "local_st": ".15",
                    "boaters_local_avg_st": "0.15",
                    "boaters_kimarite_starts": 20,
                    "boaters_escape_rate": 50.0,
                    "motor_recent": {"available": True, "runs_count": 10},
                    "recent_course_6m": {"status": "available"},
                }
            )
        races.append(
            {
                "race": race_no,
                "deadline": "10:00",
                "type": "予選",
                "title": "桐生",
                "racers": racers,
                "entries": copy.deepcopy(racers),
                "setsukan": [
                    {
                        "lane": racer["lane"],
                        "season_runs": copy.deepcopy(racer["season_runs"]),
                        "season_groups": copy.deepcopy(racer["season_groups"]),
                    }
                    for racer in racers
                ],
                "entry_changes": [{"lane": 2, "actual_course": 1}],
                "prediction": {"marker": "keep"},
                "live": {"entry": "keep"},
                "odds": {"1-2-3": 10.0},
                "result": {"status": "ok"},
            }
        )
    return {
        "venueId": "kiryu",
        "venue": "桐生",
        "date": "2026-11-18",
        "races": races,
        "preds": {"1": {"prediction_history": {"morning": [1]}}},
        "predictionStatus": "ready",
    }


def tracked_json(path: str) -> dict:
    local = ROOT / path
    if local.is_file():
        return json.loads(local.read_text(encoding="utf-8"))
    raw = subprocess.check_output(["git", "show", f"HEAD:{path}"], cwd=ROOT)
    return json.loads(raw.decode("utf-8"))


class OfficialMorningTests(unittest.TestCase):
    def test_all_twelve_venues_have_official_codes_and_urls(self) -> None:
        self.assertEqual(len(official.VENUES), 12)
        self.assertEqual(set(code for code, _name in official.VENUES.values()), {
            "01", "02", "04", "08", "11", "14", "19", "20", "21", "22", "23", "24"
        })
        for slug, (code, _name) in official.VENUES.items():
            for race_no in range(1, 13):
                with self.subTest(slug=slug, race_no=race_no):
                    url = official.official_url(slug, "2026-11-18", race_no)
                    self.assertIn(f"jcd={code}", url)
                    self.assertIn(f"rno={race_no}", url)
                    self.assertIn("hd=20261118", url)

    def test_parser_converts_precision_and_accident_finish(self) -> None:
        race = official.parse_official_html(
            official_fixture(special_finish="転"),
            slug="kiryu",
            target_date="2026-11-18",
            race_no=1,
        )
        self.assertEqual(len(race["racers"]), 6)
        first = race["racers"][0]
        self.assertEqual((first["nat_2"], first["motor_2"]), ("45.6", "35.1"))
        self.assertEqual(first["season_runs"][0], {
            "race": "1R", "course": "2", "entry_course": "2", "st": ".09", "finish": "転"
        })
        self.assertEqual(first["season_groups"][0]["day"], "初日")

    def test_parser_rejects_incomplete_entry(self) -> None:
        with self.assertRaisesRegex(ValueError, "racer count"):
            official.parse_official_html(
                official_fixture(missing_lane=6),
                slug="kiryu",
                target_date="2026-11-18",
                race_no=1,
            )

    def test_merge_preserves_live_predictions_supplements_and_entry_changes(self) -> None:
        baseline = synthetic_payload()
        before = official.protected_projection(baseline)
        observed = official.parse_official_html(
            official_fixture(special_finish="F"),
            slug="kiryu",
            target_date="2026-11-18",
            race_no=1,
        )
        candidate = official.merge_official_races(
            baseline, [observed], slug="kiryu", target_date="2026-11-18"
        )
        self.assertEqual(official.protected_projection(candidate), before)
        self.assertEqual(candidate["races"][0]["entry_changes"], baseline["races"][0]["entry_changes"])
        self.assertEqual(candidate["races"][0]["racers"][0]["actual_course"], 1)
        self.assertEqual(candidate["races"][0]["racers"][0]["season_runs"][0]["finish"], "F")
        self.assertEqual(candidate["races"][0]["entries"][0]["season_runs"][0]["finish"], "F")
        self.assertEqual(candidate["races"][0]["setsukan"][0]["season_runs"][0]["finish"], "F")
        self.assertEqual(official.validate_candidate(baseline, candidate, requested_races=[1]), [])
        self.assertEqual(existing_validator.payload_error(candidate, {"reason": "ok"}), "")

    def test_20261118_kiryu_and_tokoname_existing_payloads_are_input_compatible(self) -> None:
        for slug in ("kiryu", "tokoname"):
            with self.subTest(slug=slug):
                baseline = tracked_json(f"data/venues/{slug}/20261118.json")
                observed = []
                for race_no in (1, 12):
                    race = copy.deepcopy(baseline["races"][race_no - 1])
                    race["officialEventTitle"] = "11/18互換性検証"
                    race["source"] = official.official_url(slug, "2026-11-18", race_no)
                    observed.append(race)
                candidate = official.merge_official_races(
                    baseline, observed, slug=slug, target_date="2026-11-18"
                )
                self.assertEqual(
                    official.validate_candidate(baseline, candidate, requested_races=[1, 12]), []
                )
                comparison = official.compare_core(baseline, observed)
                self.assertTrue(all(item["rate"] == 100.0 for item in comparison.values()))

    def test_missing_race_retry_reuses_every_successful_cached_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline = root / "baseline" / "kiryu" / "20261118.json"
            baseline.parent.mkdir(parents=True)
            baseline.write_text(
                json.dumps(synthetic_payload(), ensure_ascii=False), encoding="utf-8"
            )
            cache = root / "cache"
            first_client = FakeClient({7})
            first = official.run_validation(
                target_date="2026-11-18",
                venues=["kiryu"],
                race_numbers=list(range(1, 13)),
                baseline_root=root / "baseline",
                output_root=root / "first",
                cache_root=cache,
                client=first_client,
            )
            self.assertEqual(first["status"], "failed")
            self.assertEqual(first["venues"]["kiryu"]["missingRaces"], [7])
            self.assertFalse((root / "first/candidates/kiryu/20261118.json").exists())

            retry_client = FakeClient()
            second = official.run_validation(
                target_date="2026-11-18",
                venues=["kiryu"],
                race_numbers=list(range(1, 13)),
                baseline_root=root / "baseline",
                output_root=root / "second",
                cache_root=cache,
                client=retry_client,
            )
            self.assertEqual(retry_client.calls, [7])
            self.assertEqual(second["status"], "ok")
            self.assertEqual(second["summary"]["completeVenueCount"], 1)
            candidate = json.loads(
                (root / "second/candidates/kiryu/20261118.json").read_text(encoding="utf-8")
            )
            self.assertEqual(existing_validator.payload_error(candidate, {"reason": "ok"}), "")

    def test_no_same_date_baseline_builds_strict_candidate_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            history = root / "baseline" / "kiryu" / "20261117.json"
            history.parent.mkdir(parents=True)
            history.write_text(
                json.dumps(synthetic_payload(), ensure_ascii=False), encoding="utf-8"
            )
            report = official.run_validation(
                target_date="2026-11-18",
                venues=["kiryu"],
                race_numbers=list(range(1, 13)),
                baseline_root=root / "baseline",
                output_root=root / "output",
                cache_root=root / "cache",
                client=FakeClient(),
            )
            self.assertEqual(report["status"], "ok")
            venue = report["venues"]["kiryu"]
            self.assertEqual(venue["buildMode"], "official_with_historical_supplements")
            candidate = json.loads(
                (root / "output/candidates/kiryu/20261118.json").read_text(encoding="utf-8")
            )
            self.assertEqual(existing_validator.payload_error(candidate, {"reason": "ok"}), "")
            self.assertNotIn("preds", candidate)
            self.assertNotIn("live", candidate["races"][0])


if __name__ == "__main__":
    unittest.main()
