from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import math
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from automation.wakamatsu_v3_frozen_inference import predict_live as frozen_predict_live
from automation.wakamatsu_v3_live import (
    fetch_validated_tide,
    normalized_live_row,
    predict_v3,
    tide_input_for_race,
    validate_tide_payload,
)


JST = ZoneInfo("Asia/Tokyo")
FIXTURE = Path(__file__).parent / "fixtures" / "wakamatsu_v3_zero_local_cases.json"


def tide_html(date_compact: str = "20261014") -> str:
    return f"""
    <select name="date"><option value="{date_compact}" selected>対象日</option></select>
    <table><tr><td>日出</td><td>06:20</td></tr></table>
    <table>
      <tr><td>潮時</td><td>05:39</td><td>11:59</td><td>17:19</td><td>23:19</td></tr>
      <tr><td>潮位</td><td>干潮</td><td>満潮</td><td>干潮</td><td>満潮</td></tr>
      <tr><td>潮位</td><td>35cm</td><td>125cm</td><td>60cm</td><td>153cm</td></tr>
    </table>
    <table>
      <tr><td>日時</td><td>満潮時刻</td><td>満潮位</td><td>干潮時刻</td><td>干潮位</td><td>潮名</td></tr>
      <tr><td>10月15日(木)</td><td>00:10 13:00</td><td>146cm 105cm</td><td>06:10 17:39</td><td>42cm 66cm</td><td>中潮</td></tr>
    </table>
    """


def payload(date_text: str, events: list[tuple[str, str]]) -> dict:
    next_date = (date.fromisoformat(date_text) + timedelta(days=1)).isoformat()
    return {
        "status": "ok",
        "reason": "validated_fresh_fetch",
        "targetDate": date_text,
        "sourceDate": date_text,
        "fetchedAt": f"{date_text}T12:00:00+09:00",
        "sourceUrl": "https://tide.chowari.jp/40/401005/20852/",
        "fresh": True,
        "events": [
            {"date": date_text, "type": kind, "time": time, "level": 100}
            for kind, time in events
        ] + [
            {"date": next_date, "type": "low", "time": "06:10", "level": 100},
            {"date": next_date, "type": "high", "time": "12:29", "level": 100},
        ],
    }


def regression_cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class WakamatsuV3LiveTests(unittest.TestCase):
    def test_fresh_fetch_records_jst_and_confirms_page_date(self):
        result = fetch_validated_tide(
            "2026-10-14",
            now=datetime(2026, 10, 14, 9, 0, tzinfo=JST),
            opener=lambda _url: (
                "https://tide.chowari.jp/40/401005/20852/",
                tide_html(),
            ),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sourceDate"], "2026-10-14")
        self.assertEqual(result["fetchedAt"], "2026-10-14T09:00:00+09:00")
        self.assertEqual(len(result["events"]), 8)
        self.assertEqual(result["events"][4]["date"], "2026-10-15")

    def test_fetch_failure_never_returns_cached_data(self):
        def fail(_url):
            raise TimeoutError("offline")

        result = fetch_validated_tide(
            "2026-10-14",
            now=datetime(2026, 10, 14, 9, 0, tzinfo=JST),
            opener=fail,
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "fetch_failed:TimeoutError")
        self.assertNotIn("events", result)

    def test_previous_day_cache_is_rejected(self):
        value = payload("2026-10-14", [("high", "11:59"), ("low", "17:19")])
        value["fetchedAt"] = "2026-10-13T23:59:00+09:00"
        self.assertEqual(
            validate_tide_payload(value, "2026-10-14"),
            "tide_stale_for_target_date",
        )

    def test_fetch_date_must_match_target_race_date(self):
        result = fetch_validated_tide(
            "2026-10-14",
            now=datetime(2026, 10, 13, 23, 59, tzinfo=JST),
            opener=lambda _url: (_url, tide_html()),
        )
        self.assertEqual(result["reason"], "fetch_date_mismatch")
        self.assertFalse(result["fresh"])

    def test_same_day_unknown_freshness_is_rejected(self):
        value = payload("2026-10-14", [("high", "11:59"), ("low", "17:19")])
        value.pop("fetchedAt")
        value.pop("fresh")
        self.assertEqual(
            validate_tide_payload(value, "2026-10-14"),
            "tide_freshness_unverified",
        )

    def test_page_date_mismatch_is_rejected(self):
        result = fetch_validated_tide(
            "2026-10-14",
            now=datetime(2026, 10, 14, 9, 0, tzinfo=JST),
            opener=lambda _url: ("https://example.test/", tide_html("20261015")),
        )
        self.assertEqual(result["reason"], "page_date_mismatch")
        self.assertEqual(result["sourceDate"], "2026-10-15")

    def test_page_date_unverified_is_rejected(self):
        result = fetch_validated_tide(
            "2026-10-14",
            now=datetime(2026, 10, 14, 9, 0, tzinfo=JST),
            opener=lambda _url: ("https://example.test/", "<html>日付なし</html>"),
        )
        self.assertEqual(result["reason"], "page_date_unverified")

    def test_missing_required_adjacent_extrema_is_rejected(self):
        value = payload(
            "2026-10-14",
            [("low", "05:39"), ("high", "11:59"), ("low", "17:19")],
        )
        value["events"] = [
            event for event in value["events"] if event["date"] == "2026-10-14"
        ]
        result = tide_input_for_race(
            value, "2026-10-14", "18:00", tide_phase="rising"
        )
        self.assertEqual(result["reason"], "required_tide_extrema_missing")

    def test_boundary_phase_is_derived_from_verified_extrema(self):
        value = payload(
            "2026-10-14",
            [("low", "05:39"), ("high", "11:59"), ("low", "17:19"), ("high", "23:19")],
        )
        cases = (
            ("23:14", "rising"),
            ("23:19", "falling"),
            ("23:24", "falling"),
            ("17:14", "falling"),
            ("17:19", "rising"),
            ("17:24", "rising"),
        )
        for deadline, expected in cases:
            with self.subTest(deadline=deadline, expected=expected):
                result = tide_input_for_race(
                    value, "2026-10-14", deadline
                )
                self.assertEqual(result["status"], "ok")
                self.assertTrue(result["boundary"])
                self.assertEqual(result["tide_phase"], expected)

    def test_historical_exact_extrema_follow_outgoing_phase(self):
        cases = (
            ("2026-08-08", "19:10", "high", "falling"),
            ("2026-08-31", "17:39", "low", "rising"),
            ("2026-10-14", "17:19", "low", "rising"),
        )
        for target_date, deadline, kind, expected in cases:
            events = (
                [
                    ("low", "05:30"),
                    ("high", "12:00"),
                    ("low", deadline),
                    ("high", "23:30"),
                ]
                if kind == "low"
                else [("low", "06:00"), ("high", deadline), ("low", "23:00")]
            )
            with self.subTest(target_date=target_date, deadline=deadline):
                result = tide_input_for_race(
                    payload(target_date, events), target_date, deadline
                )
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["tide_phase"], expected)

    def test_wakamatsu_20261021_r4_exact_high_is_falling(self):
        value = payload(
            "2026-10-21",
            [("high", "05:40"), ("low", "13:00"), ("high", "19:30")],
        )
        result = tide_input_for_race(
            value,
            "2026-10-21",
            "19:30",
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["boundary"])
        self.assertEqual(result["tide_phase"], "falling")

    def test_boundary_phase_conflict_is_rejected(self):
        value = payload(
            "2026-10-14",
            [("low", "05:39"), ("high", "11:59"), ("low", "17:19"), ("high", "23:19")],
        )
        result = tide_input_for_race(
            value,
            "2026-10-14",
            "17:19",
            tide_phase="falling",
        )
        self.assertEqual(result["reason"], "tide_phase_mismatch")

    def test_missing_upstream_phase_is_recovered_from_validated_extrema(self):
        value = payload(
            "2026-10-14",
            [("low", "05:39"), ("high", "11:59"), ("low", "17:19"), ("high", "23:19")],
        )
        result = tide_input_for_race(value, "2026-10-14", "16:00")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tide_phase"], "falling")
        with self.assertRaisesRegex(ValueError, "missing validated tide tide_phase"):
            frozen_predict_live(
                {
                    "boat1_live_win_pct": 50,
                    "boat1_original_lap_time": 38,
                    "boat1_original_turn_time": 6,
                },
                {"minutes_from_high_tide": 10, "minutes_from_low_tide": 10},
                strict=True,
            )

    def test_missing_upstream_phase_at_boundary_is_recovered(self):
        value = payload(
            "2026-10-14",
            [("low", "05:39"), ("high", "11:59"), ("low", "17:19"), ("high", "23:19")],
        )
        result = tide_input_for_race(value, "2026-10-14", "17:19")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tide_phase"], "rising")

    def test_zero_local_win_regressions_and_input_immutability(self):
        for case in regression_cases():
            row = case["row"]
            before = deepcopy(row)
            tide = payload(
                case["date"],
                [(event["type"], event["time"]) for event in case["events"]],
            )
            with self.subTest(date=case["date"], race=row["race_no"]):
                result = predict_v3(
                    row,
                    tide,
                    target_date=case["date"],
                    deadline=case["deadline"],
                    input_stage="live",
                )
                self.assertEqual(result["status"], "ok")
                self.assertTrue(
                    math.isclose(
                        result["lane1FlyProbability"],
                        case["expected"],
                        abs_tol=0.0005,
                    )
                )
                self.assertEqual(normalized_live_row(row)["boat1_local_win_rate"], "")
                self.assertEqual(row, before)

    def test_morning_is_unavailable_and_live_features_are_required(self):
        case = regression_cases()[0]
        tide = payload(
            case["date"],
            [(event["type"], event["time"]) for event in case["events"]],
        )
        morning = predict_v3(
            case["row"],
            tide,
            target_date=case["date"],
            deadline=case["deadline"],
            input_stage="morning",
        )
        self.assertEqual(morning["status"], "unavailable")
        self.assertIsNone(morning["lane1FlyProbability"])
        incomplete = dict(case["row"])
        incomplete["boat1_original_lap_time"] = ""
        live = predict_v3(
            incomplete,
            tide,
            target_date=case["date"],
            deadline=case["deadline"],
            input_stage="live",
        )
        self.assertEqual(live["status"], "unavailable")
        self.assertIsNone(live["lane1FlyProbability"])

    def test_local_win_validity_range(self):
        for value in ("0", 0, "", None, "nan", float("inf"), -1, 10.1):
            with self.subTest(invalid=value):
                self.assertEqual(
                    normalized_live_row({"boat1_local_win_rate": value})[
                        "boat1_local_win_rate"
                    ],
                    "",
                )
        for value in (0.01, 5.98, 10):
            with self.subTest(valid=value):
                self.assertEqual(
                    normalized_live_row({"boat1_local_win_rate": value})[
                        "boat1_local_win_rate"
                    ],
                    value,
                )


if __name__ == "__main__":
    unittest.main()
