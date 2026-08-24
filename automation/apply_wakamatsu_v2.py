from __future__ import annotations
from copy import deepcopy

import argparse
import json
import math
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = REPO_ROOT / "engines" / "wakamatsu_v2"
ENGINE_DIR = ENGINE_ROOT / "engine"
DB_PATH = ENGINE_ROOT / "data" / "wakamatsu_master_v1.sqlite"

LANES = (1, 2, 3, 4, 5, 6)

if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from wakamatsu_engine import WakamatsuEngine  # noqa: E402

from wakamatsu_v2_1_adjustments import (
    ENGINE_ID,
    ENGINE_VERSION,
    apply_v21_adjustments,
)


def number(value: Any, default: float | None = 0.0) -> float | None:
    if value in (None, "", "-", "―"):
        return default
    try:
        parsed = float(re.sub(r"[^0-9.\-]", "", str(value)))
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def integer(value: Any, default: int = 0) -> int:
    parsed = number(value, None)
    return default if parsed is None else int(parsed)


def normalize_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\s　・･]+", "", text)
    return re.sub(r"\d+$", "", text)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class PlayerIdResolver:
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(str(db_path))
        self.by_name: dict[str, int] = {}
        self._load()
        self.by_name.update(
            {
                normalize_name("\u5ddd\u5408 \u7406\u53f8"): 3839,
                normalize_name("\u4f50\u85e4 \u4e16\u90a3"): 5359,
                normalize_name("\u77f3\u5ca1 \u5c06\u592a"): 4550,
                normalize_name("\u9060\u85e4 \u572d\u543e"): 5186,
            }
        )

    def _load(self) -> None:
        table_names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        candidates = (
            "player_course_summary",
            "player_course",
            "player_master",
            "players",
        )
        for table in candidates:
            if table not in table_names:
                continue
            columns = {
                row[1]
                for row in self.conn.execute(f'PRAGMA table_info("{table}")')
            }
            id_col = next(
                (x for x in ("player_id", "reg_no", "registration_no", "racer_id") if x in columns),
                None,
            )
            name_col = next(
                (x for x in ("player_name", "name", "racer_name") if x in columns),
                None,
            )
            if not id_col or not name_col:
                continue
            query = f'SELECT DISTINCT "{id_col}", "{name_col}" FROM "{table}"'
            for player_id, player_name in self.conn.execute(query):
                key = normalize_name(player_name)
                if key and player_id not in (None, ""):
                    self.by_name.setdefault(key, int(player_id))

    def resolve(self, racer: dict) -> int | None:
        for key in ("player_id", "reg_no", "registration_no", "racer_id", "register_no"):
            value = racer.get(key)
            if value not in (None, ""):
                parsed = integer(value, 0)
                if parsed > 0:
                    return parsed
        return self.by_name.get(normalize_name(racer.get("name")))

    def close(self) -> None:
        self.conn.close()


def merge_live_files(
    payload: dict,
    target_date: str,
    data_root: Path,
) -> dict:
    # Live collector stores raw files outside the publisher repository.
    vps_live_root = Path("/opt/sinz-edge/data/live")
    repo_live_root = data_root / "live"
    base_live_root = vps_live_root if vps_live_root.exists() else repo_live_root
    live_root = base_live_root / target_date / "wakamatsu"

    for race in payload.get("races") or []:
        race_no = integer(race.get("race"))
        race_dir = live_root / f"{race_no:02d}"

        live = race.get("live")
        if not isinstance(live, dict):
            live = {}

        files = {
            "direct": race_dir / "direct.json",
            "exhibition": race_dir / "exhibition.json",
            "original_exhibition": race_dir / "original_exhibition.json",
            "result": race_dir / "result.json",
        }

        for kind, file_path in files.items():
            if not file_path.is_file():
                continue

            try:
                wrapper = json.loads(
                    file_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue

            if (
                wrapper.get("status") != "complete"
                or wrapper.get("complete") is not True
            ):
                continue

            data = wrapper.get("data")
            if not isinstance(data, dict):
                continue

            if kind == "direct":
                # Keep the complete direct-information object.
                # The engine expects live["weather"] to be a dictionary,
                # not the weather label string such as "晴".
                live["direct"] = data
                live["weather"] = data

                # Entry helpers are also read directly from the live root.
                for field in (
                    "actual_entry",
                    "entry_changed",
                    "withdrawals",
                    "stabilizer",
                    "lap_shortened",
                    "other_changes",
                    "racers",
                ):
                    if field in data:
                        live[field] = data[field]

                race["direct"] = data

            elif kind == "exhibition":
                live["exhibition"] = data
                race["exhibition"] = data

            elif kind == "original_exhibition":
                live["original"] = data
                live["original_exhibition"] = data
                race["original"] = data
                race["original_exhibition"] = data

            elif kind == "result":
                live["result"] = data
                race["result"] = data

        race["live"] = live

    return payload


def validate_payload(payload: dict, target_date: str) -> None:
    if payload.get("venueId") != "wakamatsu":
        raise RuntimeError("not_wakamatsu_payload")
    if payload.get("date") != target_date:
        raise RuntimeError(
            f"date_mismatch: expected={target_date} actual={payload.get('date')}"
        )
    races = payload.get("races")
    if not isinstance(races, list) or len(races) != 12:
        raise RuntimeError("wakamatsu_races_must_be_12")
    numbers = sorted(integer(race.get("race")) for race in races)
    if numbers != list(range(1, 13)):
        raise RuntimeError(f"invalid_race_numbers: {numbers}")
    for race in races:
        racers = race.get("racers")
        if not isinstance(racers, list) or len(racers) != 6:
            raise RuntimeError(f"race_{race.get('race')}_racers_must_be_6")
        lanes = sorted(integer(racer.get("lane")) for racer in racers)
        if lanes != list(LANES):
            raise RuntimeError(f"race_{race.get('race')}_lanes_invalid: {lanes}")


def actual_entry_map(race: dict) -> dict[int, int]:
    live = race.get("live") or {}
    actual = (
        live.get("actual_entry")
        or live.get("actualEntry")
        or race.get("actual_entry")
        or race.get("actualEntry")
    )
    if isinstance(actual, list) and len(actual) == 6:
        return {integer(lane): course for course, lane in enumerate(actual, start=1)}
    return {
        integer(racer.get("lane")): integer(
            racer.get("actual_course")
            or racer.get("entry_course")
            or racer.get("lane")
        )
        for racer in race.get("racers") or []
    }


def entries_from_live(race: dict, kind: str) -> list[dict]:
    live = race.get("live") or {}
    if kind == "exhibition":
        candidates = [
            (live.get("exhibition") or {}).get("entries"),
            live.get("exhibition_entries"),
            (race.get("exhibition") or {}).get("entries")
            if isinstance(race.get("exhibition"), dict)
            else None,
        ]
    else:
        candidates = [
            (live.get("original") or {}).get("entries"),
            (live.get("original_exhibition") or {}).get("entries"),
            live.get("original_entries"),
            (race.get("original") or {}).get("entries")
            if isinstance(race.get("original"), dict)
            else None,
            (race.get("original_exhibition") or {}).get("entries")
            if isinstance(race.get("original_exhibition"), dict)
            else None,
        ]
    for value in candidates:
        if isinstance(value, list):
            return value
    return []

def final_inputs_ready(race: dict) -> tuple[bool, list[str]]:
    missing: list[str] = []

    exhibition = entries_from_live(race, "exhibition")

    if len(exhibition) != 6:
        missing.append("exhibition_entries")

    lanes = sorted(
        integer(
            entry.get("lane")
            or entry.get("boat_no")
            or entry.get("boat")
        )
        for entry in exhibition
    )

    if lanes != list(LANES):
        missing.append("exhibition_lanes")

    times = [
        number(
            first_present(
                entry,
                "exhibition_time",
                "display_time",
                "time",
            ),
            None,
        )
        for entry in exhibition
    ]

    if len(times) != 6 or any(value is None for value in times):
        missing.append("exhibition_times")

    entry_map = actual_entry_map(race)

    if sorted(entry_map) != list(LANES):
        missing.append("actual_entry")

    return not missing, missing

def entry_index(entries: list[dict]) -> dict[int, dict]:
    result = {}
    for entry in entries:
        lane = integer(entry.get("lane") or entry.get("boat_no") or entry.get("boat"))
        if lane in LANES:
            result[lane] = entry
    return result


def lower_is_better_scores(
    index: dict[int, dict],
    keys: tuple[str, ...],
) -> dict[int, float]:
    values: dict[int, float] = {}
    for lane, entry in index.items():
        value = None
        for key in keys:
            value = number(entry.get(key), None)
            if value is not None:
                break
        if value is not None and value > 0:
            values[lane] = value
    if len(values) < 2:
        return {lane: 0.0 for lane in LANES}
    ordered = sorted(values, key=values.get)
    rank = {lane: i for i, lane in enumerate(ordered)}
    denominator = max(1, len(ordered) - 1)
    return {
        lane: round(1.0 - 2.0 * rank[lane] / denominator, 6)
        if lane in rank
        else 0.0
        for lane in LANES
    }


def original_scores(index: dict[int, dict]) -> dict[int, float]:
    components = []
    for keys in (
        ("lap_time", "lap"),
        ("turn_time", "turn"),
        ("straight_time", "straight"),
    ):
        components.append(lower_is_better_scores(index, keys))
    return {
        lane: round(sum(component[lane] for component in components) / len(components), 6)
        for lane in LANES
    }


def weather_context(payload: dict, race: dict) -> dict:
    live = race.get("live") or {}
    return (
        race.get("weather")
        or live.get("weather")
        or payload.get("weather")
        or {}
    )


def tide_context(payload: dict, race: dict) -> dict:
    return race.get("tide") or payload.get("tide") or {}


def first_present(source: dict, *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def day_no(payload: dict, race: dict) -> int:
    value = (
        race.get("eventDay")
        or (race.get("race_meta") or {}).get("day_no")
        or payload.get("eventDay")
    )
    parsed = integer(value, 0)
    return parsed if parsed > 0 else 1


def meeting_runs(racer: dict) -> list[dict]:
    runs = racer.get("meeting_runs") or racer.get("season_runs") or []
    result = []
    for run in runs:
        result.append(
            {
                "race": run.get("race"),
                "course": integer(
                    run.get("entry_course") or run.get("course"),
                    0,
                ),
                "entry_course": integer(
                    run.get("entry_course") or run.get("course"),
                    0,
                ),
                "st": number(run.get("st"), None),
                "finish": run.get("finish"),
            }
        )
    return result


def build_engine_input(
    payload: dict,
    race: dict,
    resolver: PlayerIdResolver,
) -> tuple[dict, list[dict]]:
    race_no = integer(race.get("race"))
    entry_map = actual_entry_map(race)
    exhibition = entry_index(entries_from_live(race, "exhibition"))
    original = entry_index(entries_from_live(race, "original"))
    exhibition_scores = lower_is_better_scores(
        exhibition,
        ("exhibition_time", "display_time", "time"),
    )
    original_score_map = original_scores(original)

    unresolved = []
    boats = []
    for racer in sorted(race.get("racers") or [], key=lambda x: integer(x.get("lane"))):
        lane = integer(racer.get("lane"))
        player_id = resolver.resolve(racer)
        if player_id is None:
            unresolved.append(
                {
                    "lane": lane,
                    "name": racer.get("name"),
                    "reason": "player_id_not_found",
                }
            )

        ex = exhibition.get(lane, {})
        original_entry = original.get(lane, {})
        boats.append(
            {
                "lane": lane,
                "entry_course": entry_map.get(lane, lane),
                "player_id": player_id,
                "name": racer.get("name"),
                "class": racer.get("class"),
                "nat_win": number(racer.get("nat_win"), 0.0),
                "nat_2": number(racer.get("nat_2"), 0.0),
                "nat_3": number(racer.get("nat_3"), 0.0),
                "local_win": number(racer.get("local_win"), 0.0),
                "local_2": number(racer.get("local_2"), 0.0),
                "local_3": number(racer.get("local_3"), 0.0),
                "avg_st": number(racer.get("avg_st"), 0.18),
                "local_st": number(racer.get("local_st"), None),
                "motor_2": number(racer.get("motor_2"), 33.0),
                "motor_3": number(racer.get("motor_3"), 50.0),
                "boat_2": number(racer.get("boat_2"), 33.0),
                "boat_3": number(racer.get("boat_3"), 50.0),
                "meeting_runs": meeting_runs(racer),
                "season_runs": meeting_runs(racer),
                "exhibition_score": exhibition_scores.get(lane, 0.0),
                "original_exhibition_score": original_score_map.get(lane, 0.0),
                "original_exhibition": first_present(
                    original_entry,
                    "sum",
                    "original_sum",
                    "lap_time",
                    "turn_time",
                    "straight_time",
                ),
                "start_time": number(
                    first_present(ex, "start_time", "exhibition_st", "st"),
                    None,
                ),
                "tilt": number(first_present(ex, "tilt"), number(racer.get("tilt"), 0.0)),
            }
        )

    weather = weather_context(payload, race)
    tide = tide_context(payload, race)
    title = str(race.get("title") or race.get("type") or "")
    event_day = day_no(payload, race)

    race_input = {
        "date": payload.get("date"),
        "race_no": race_no,
        "start_time": race.get("deadline"),
        "event_day": event_day,
        "race_stage": race.get("race_stage"),
        "is_semifinal": bool(
            race.get("is_semifinal")
            or "準優" in title
            or "準優勝" in title
        ),
        "is_final": bool(
            race.get("is_final")
            or "優勝戦" in title
        ),
        "tide_type": first_present(
            tide,
            "tideType",
            "tide_type",
            "type",
            "name",
        ),
        "tide_phase": (
            race.get("tide_phase")
            or first_present(tide, "phase", "tidePhase", "tide_phase", "label")
        ),
        "tide_zone": (
            race.get("tide_zone")
            or first_present(tide, "zone", "tideZone", "tide_zone")
        ),
        "minutes_to_low_tide": (
            race.get("minutes_to_low_tide")
            or first_present(tide, "minutesToLowTide", "minutes_to_low_tide")
        ),
        "wind_dir": first_present(
            weather,
            "wind_dir",
            "windDirection",
            "wind_direction",
            "wind",
        ),
        "wind_speed": number(
            first_present(weather, "wind_speed", "windSpeed", "wind"),
            0.0,
        ),
        "wave_height": number(
            first_present(weather, "wave_height", "waveHeight", "wave"),
            0.0,
        ),
        "boats": boats,
    }
    return race_input, unresolved


def probability_maps(result: dict) -> tuple[dict, dict, dict, dict]:
    rows = result.get("probabilities") or []
    if len(rows) != 6:
        raise RuntimeError("wakamatsu_engine_probabilities_must_be_6")

    def make(key: str) -> dict[str, float]:
        values = {
            str(integer(row.get("lane"))): round(float(row.get(key) or 0.0) * 100.0, 2)
            for row in rows
        }
        if set(values) != {str(lane) for lane in LANES}:
            raise RuntimeError(f"{key}_lanes_invalid")
        difference = round(100.0 - sum(values.values()), 2)
        if abs(difference) <= 0.1:
            largest = max(values, key=values.get)
            values[largest] = round(values[largest] + difference, 2)
        if abs(sum(values.values()) - 100.0) > 0.2:
            raise RuntimeError(f"{key}_sum_invalid: {sum(values.values())}")
        return values

    win = make("win")
    second = make("second")
    third = make("third")
    top3 = {
        str(integer(row.get("lane"))): round(float(row.get("top3") or 0.0) * 100.0, 2)
        for row in rows
    }
    return win, second, third, top3


def ticket_rows(rows: list[dict], role: str) -> list[dict]:
    result = []
    for row in rows:
        combo = row.get("ticket") or row.get("combo")
        if not combo:
            continue
        result.append(
            {
                "combo": combo,
                "role": role,
                "probability": round(float(row.get("probability") or 0.0) * 100.0, 3),
                "scenarioTags": row.get("scenario_tags") or [],
                "odds": "-",
            }
        )
    return result


def site_prediction(result: dict, unresolved: list[dict]) -> dict:
    win, second, third, top3 = probability_maps(result)
    tickets = result.get("tickets") or {}
    main = ticket_rows(tickets.get("main") or [], "本線")
    deviation = ticket_rows(tickets.get("deviation") or [], "ズレ対応")
    upset = ticket_rows(tickets.get("upset") or [], "荒れ対応")
    all_tickets = main + deviation + upset
    if len(all_tickets) != 10:
        raise RuntimeError(f"wakamatsu_ticket_count_invalid: {len(all_tickets)}")
    if len({row["combo"] for row in all_tickets}) != 10:
        raise RuntimeError("wakamatsu_tickets_not_unique")

    sab_detail = result.get("sab") or {}
    sab_grade = sab_detail.get("grade") if isinstance(sab_detail, dict) else sab_detail
    if not sab_grade:
        raise RuntimeError("wakamatsu_sab_missing")

    ranked = sorted(win, key=win.get, reverse=True)
    return {
        "status": "complete",
        "engine": ENGINE_ID,
        "engineVersion": ENGINE_VERSION,
        "win": win,
        "second": second,
        "third": third,
        "top3": top3,
        "sab": sab_grade,
        "sabDetail": sab_detail,
        "confidence": round(float(sab_detail.get("score") or 0.0) * 100.0, 1)
        if isinstance(sab_detail, dict)
        else None,
        "ai": main + deviation,
        "aiUpset": upset,
        "tickets": all_tickets,
        "scenarios": result.get("scenarios") or {},
        "raceContext": result.get("race_context") or {},
        "stageWeighting": result.get("stage_weighting") or {},
        "meetingFormAudit": result.get("meeting_form_audit") or [],
        "readability": {
            "axisLane": int(ranked[0]),
            "secondHeadLane": int(ranked[1]),
            "axisGap": round(win[ranked[0]] - win[ranked[1]], 2),
        },
        "diagnostics": {
            "playerIdResolution": {
                "unresolved": unresolved,
                "resolvedCount": 6 - len(unresolved),
                "unresolvedCount": len(unresolved),
            },
            "fullReflection": result.get("full_reflection") or {},
            "validation": result.get("validation") or {},
            "slitAdjacency": result.get("slit_adjacency_adjustments") or [],
            "escapeRateMultiAttack": result.get("escape_rate_multi_attack") or {},
            "originalExhibitionOuterLink": result.get("original_exhibition_outer_link") or {},
            "ticketRankAudit": result.get("v22_ticket_rank_audit") or [],
            "oddsUsedForPrediction": False,
        },
    }


def apply_wakamatsu_v2(payload: dict, target_date: str) -> dict:
    validate_payload(payload, target_date)
    resolver = PlayerIdResolver(DB_PATH)
    engine = WakamatsuEngine(DB_PATH)
    predictions = {}
    failures = []
    unresolved_all = []

    try:
        for race in payload["races"]:
            race_no = integer(race.get("race"))
            try:
                race_input, unresolved = build_engine_input(payload, race, resolver)
                ready, missing_final_inputs = final_inputs_ready(race)
                existing_pre = race.get("predictionPre")

                if ready:
                    final_result = engine.predict(race_input)
                    final_result = apply_v21_adjustments(
                        final_result,
                        race_input,
                    )

                    final_prediction = site_prediction(
                        final_result,
                        unresolved,
                    )
                    final_prediction["phase"] = "final"
                    final_prediction["finalPredictionStatus"] = "complete"
                    final_prediction["predictionStage"] = {
                        "label": "本予想",
                        "badge": "本予想",
                        "statusText": "直前・展示を反映して若松v2.2で再精査済み",
                        "color": "green",
                    }
                    final_prediction["missingFinalInputs"] = []

                    if existing_pre is None:
                        pre_input = deepcopy(race_input)

                        for boat in pre_input.get("boats") or []:
                            boat["exhibition_score"] = 0.0
                            boat["original_exhibition_score"] = 0.0
                            boat["original_exhibition"] = None
                            boat["start_time"] = None

                        pre_result = engine.predict(pre_input)
                        pre_result = apply_v21_adjustments(
                            pre_result,
                            pre_input,
                        )

                        existing_pre = site_prediction(
                            pre_result,
                            unresolved,
                        )
                        existing_pre["phase"] = "pre"
                        existing_pre["finalPredictionStatus"] = "waiting_live_data"
                        existing_pre["missingFinalInputs"] = [
                            "exhibition_entries",
                            "exhibition_times",
                        ]
                    probability_review = {}
                    for lane in LANES:
                        lane_key = str(lane)

                        pre_win = number(
                            (existing_pre.get("win") or {}).get(lane_key),
                            0.0,
                        )
                        pre_second = number(
                            (existing_pre.get("second") or {}).get(lane_key),
                            0.0,
                        )
                        pre_third = number(
                            (existing_pre.get("third") or {}).get(lane_key),
                            0.0,
                        )

                        final_win = number(
                            (final_prediction.get("win") or {}).get(lane_key),
                            0.0,
                        )
                        final_second = number(
                            (final_prediction.get("second") or {}).get(lane_key),
                            0.0,
                        )
                        final_third = number(
                            (final_prediction.get("third") or {}).get(lane_key),
                            0.0,
                        )

                        probability_review[lane_key] = {
                            "morningWin": round(pre_win, 1),
                            "morningSecond": round(pre_second, 1),
                            "morningThird": round(pre_third, 1),
                            "deltaWin": round(final_win - pre_win, 1),
                            "deltaSecond": round(final_second - pre_second, 1),
                            "deltaThird": round(final_third - pre_third, 1),
                        }

                    final_prediction["probabilityReviewStatus"] = "reviewed"
                    final_prediction["probabilityFlow"] = {
                        "required": True,
                        "baseApplied": True,
                        "baseLabel": "\u76f4\u524d\u524d\u30a8\u30f3\u30b8\u30f3\u4e88\u60f3",
                        "realtimeApplied": True,
                        "realtimeLabel": "\u5c55\u793a\u30fb\u30b9\u30ea\u30c3\u30c8\u30fb\u76f4\u524d\u53cd\u6620",
                        "reviewed": True,
                        "reviewLabel": "\u518d\u7cbe\u67fb\u5f8c\u306e\u8abf\u6574\u6570\u5b57",
                        "adjustedRequired": True,
                    }
                    final_prediction["probabilityReview"] = probability_review

                    race["predictionPre"] = existing_pre
                    race["predictionFinal"] = final_prediction
                    race["prediction"] = final_prediction
                    prediction = final_prediction

                else:
                    pre_result = engine.predict(race_input)
                    pre_result = apply_v21_adjustments(
                        pre_result,
                        race_input,
                    )

                    pre_prediction = site_prediction(
                        pre_result,
                        unresolved,
                    )
                    pre_prediction["phase"] = "pre"
                    pre_prediction["finalPredictionStatus"] = "waiting_live_data"
                    pre_prediction["missingFinalInputs"] = missing_final_inputs

                    race["predictionPre"] = pre_prediction
                    race["predictionFinal"] = None
                    race["prediction"] = pre_prediction
                    prediction = pre_prediction

                predictions[str(race_no)] = prediction
                unresolved_all.extend(
                    {"race": race_no, **row}
                    for row in unresolved
                )
            except Exception as exc:
                failures.append(
                    {
                        "race": race_no,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    finally:
        engine.close()
        resolver.close()

    if failures:
        raise RuntimeError(
            "wakamatsu_v2_generation_failed: "
            + json.dumps(failures, ensure_ascii=False)
        )
    if sorted(map(int, predictions)) != list(range(1, 13)):
        raise RuntimeError("wakamatsu_v2_predictions_must_be_12")

    payload["engine"] = ENGINE_ID
    payload["engineVersion"] = ENGINE_VERSION
    payload["preds"] = predictions
    payload["predictionStatus"] = "ready"
    payload["predictionReason"] = None
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "version": ENGINE_VERSION,
        "master": str(DB_PATH.relative_to(REPO_ROOT)).replace("\\", "/"),
        "generatedBy": "automation/apply_wakamatsu_v2.py",
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
        "raceCount": 12,
        "playerIdUnresolved": unresolved_all,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument(
        "--require-open",
        action="store_true",
        help="Fail when Wakamatsu JSON does not exist instead of skipping.",
    )
    args = parser.parse_args()

    date_dir = args.date.replace("-", "")
    data_root = Path(args.data_root)
    dated_path = data_root / "venues" / "wakamatsu" / f"{date_dir}.json"
    latest_path = data_root / "venues" / "wakamatsu" / "latest.json"

    if not dated_path.exists():
        message = f"Wakamatsu data is not open: {dated_path}"
        if args.require_open:
            raise FileNotFoundError(message)
        print(message)
        return 0

    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    payload = merge_live_files(
        payload,
        args.date,
        data_root,
    )
    payload = apply_wakamatsu_v2(payload, args.date)

    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)

    summary = {
        "date": args.date,
        "engine": payload["engine"],
        "engineVersion": payload["engineVersion"],
        "raceCount": len(payload["preds"]),
        "datedPath": str(dated_path),
        "latestPath": str(latest_path),
        "playerIdUnresolved": payload["predictionEngine"]["playerIdUnresolved"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
