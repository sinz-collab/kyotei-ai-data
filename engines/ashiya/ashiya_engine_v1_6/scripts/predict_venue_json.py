from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from ashiya_engine.engine import AshiyaEngine
from ashiya_engine.utils import atomic_write_json


def time_to_minutes(value):
    """HH:MM -> midnightからの分。変換不能ならNone。"""
    if value in (None, ""):
        return None
    try:
        h, m = str(value).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        return None


def merge_by_lane(racers, entries, mapping=None):
    """
    entries の艇別データを racers へ lane 基準で統合する。
    mapping を指定した場合は {入力key: racer側key} として使用。
    """
    if not isinstance(entries, list):
        return

    by_lane = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        try:
            lane = int(item.get("lane") or 0)
        except (TypeError, ValueError):
            continue
        if 1 <= lane <= 6:
            by_lane[lane] = item

    for racer in racers:
        if not isinstance(racer, dict):
            continue
        try:
            lane = int(racer.get("lane") or 0)
        except (TypeError, ValueError):
            continue

        src = by_lane.get(lane)
        if not src:
            continue

        if mapping:
            for source_key, target_key in mapping.items():
                value = src.get(source_key)
                if value not in (None, ""):
                    racer[target_key] = value
        else:
            for key, value in src.items():
                if value not in (None, ""):
                    racer[key] = value


def apply_actual_entry(racers, actual_entry):
    """
    actual_entry は「進入コース順の艇番」。
    例 [1, 2, 4, 3, 5, 6]
      → 4号艇=3コース、3号艇=4コース。
    """
    # Reset every boat first. This prevents stale actual_course values from an
    # earlier payload from surviving when the source says entry_changed=false.
    for racer in racers:
        try:
            racer["actual_course"] = int(racer.get("lane") or 0)
        except (TypeError, ValueError):
            pass

    if not isinstance(actual_entry, list) or len(actual_entry) != 6:
        return False

    course_by_lane = {}

    for course, lane_value in enumerate(actual_entry, start=1):
        try:
            lane = int(lane_value)
        except (TypeError, ValueError):
            return False

        if not 1 <= lane <= 6 or lane in course_by_lane:
            return False
        course_by_lane[lane] = course

    if sorted(course_by_lane) != list(range(1, 7)):
        return False

    for racer in racers:
        try:
            lane = int(racer.get("lane") or 0)
        except (TypeError, ValueError):
            continue

        if lane in course_by_lane:
            racer["actual_course"] = course_by_lane[lane]
    return True


def build_tide_features(tide, deadline):
    """
    潮見表とレース締切時刻から、客観的に計算できる潮特徴だけを生成する。
    予想補正値や恣意的な内外バイアスはここでは生成しない。
    """
    if not isinstance(tide, dict):
        return {}

    out = deepcopy(tide)

    events = tide.get("events") or []
    parsed = []

    for event in events:
        if not isinstance(event, dict):
            continue

        minute = time_to_minutes(event.get("time"))
        if minute is None:
            continue

        try:
            level = float(event.get("level"))
        except (TypeError, ValueError):
            continue

        kind = str(event.get("type") or "").strip()

        parsed.append(
            {
                "type": kind,
                "minute": minute,
                "level": level,
            }
        )

    parsed.sort(key=lambda x: x["minute"])

    highs = [x for x in parsed if x["type"] == "満潮"]
    lows = [x for x in parsed if x["type"] == "干潮"]

    # 学習特徴量側が数値化するため、時刻は分へ変換して渡す。
    if len(highs) >= 1:
        out["high_tide_1_time"] = highs[0]["minute"]
        out["high_tide_1_level"] = highs[0]["level"]
        out["high_tide_time"] = highs[0]["minute"]

    if len(highs) >= 2:
        out["high_tide_2_time"] = highs[1]["minute"]
        out["high_tide_2_level"] = highs[1]["level"]

    if len(lows) >= 1:
        out["low_tide_1_time"] = lows[0]["minute"]
        out["low_tide_1_level"] = lows[0]["level"]
        out["low_tide_time"] = lows[0]["minute"]

    if len(lows) >= 2:
        out["low_tide_2_time"] = lows[1]["minute"]
        out["low_tide_2_level"] = lows[1]["level"]

    race_minute = time_to_minutes(deadline)

    if race_minute is None or not parsed:
        return out

    out["race_time"] = race_minute
    out["race_time_minutes"] = race_minute
    out["race_time_sec"] = race_minute * 60

    nearest = min(
        parsed,
        key=lambda x: abs(race_minute - x["minute"]),
    )

    out["nearest_tide_time_minutes"] = nearest["minute"]
    out["nearest_tide_level"] = nearest["level"]
    out["nearest_tide_type"] = (
        "high" if nearest["type"] == "満潮" else "low"
    )
    out["nearest_tide_minutes_diff"] = race_minute - nearest["minute"]

    nearest_high = (
        min(highs, key=lambda x: abs(race_minute - x["minute"]))
        if highs
        else None
    )
    nearest_low = (
        min(lows, key=lambda x: abs(race_minute - x["minute"]))
        if lows
        else None
    )

    if nearest_high:
        diff = race_minute - nearest_high["minute"]
        out["high_tide_minutes"] = nearest_high["minute"]
        out["minutes_from_high_tide"] = diff
        out["abs_minutes_from_high_tide"] = abs(diff)

    if nearest_low:
        diff = race_minute - nearest_low["minute"]
        out["low_tide_minutes"] = nearest_low["minute"]
        out["minutes_from_low_tide"] = diff
        out["abs_minutes_from_low_tide"] = abs(diff)

    previous_event = None
    next_event = None

    for event in parsed:
        if event["minute"] <= race_minute:
            previous_event = event

        if event["minute"] >= race_minute and next_event is None:
            next_event = event

    # 満潮→干潮 = falling
    # 干潮→満潮 = rising
    if previous_event and next_event:
        if previous_event["type"] == "満潮":
            out["tide_phase"] = "falling"
            out["tide_direction_binary"] = 0
        elif previous_event["type"] == "干潮":
            out["tide_phase"] = "rising"
            out["tide_direction_binary"] = 1

        span = next_event["minute"] - previous_event["minute"]

        if span > 0:
            progress = (
                race_minute - previous_event["minute"]
            ) / span

            tide_level = (
                previous_event["level"]
                + (
                    next_event["level"]
                    - previous_event["level"]
                )
                * progress
            )

            out["tide_level"] = round(tide_level, 3)

            out["tide_speed_proxy"] = round(
                (
                    next_event["level"]
                    - previous_event["level"]
                )
                / span,
                6,
            )

    out["tide_transition_flag"] = int(
        abs(race_minute - nearest["minute"]) <= 60
    )

    tide_type = tide.get("tideType") or tide.get("tide_type")

    tide_strength_map = {
        "大潮": 4,
        "中潮": 3,
        "小潮": 2,
        "長潮": 1,
        "若潮": 1,
    }

    if tide_type in tide_strength_map:
        out["tide_strength_class"] = tide_strength_map[tide_type]

    return out


def merge_race_payload(root: dict, race: dict) -> dict:
    payload = deepcopy(race)

    race_meta = race.get("race_meta") or {}
    live = race.get("live") or {}

    race_no = int(
        race.get("race_no")
        or race.get("race")
        or race_meta.get("race_no")
        or 0
    )

    deadline = (
        race.get("deadline")
        or race_meta.get("deadline")
        or ""
    )

    race_date = (
        race_meta.get("date")
        or root.get("date")
    )

    payload["date"] = race_date
    payload["race_date"] = race_date
    payload["race_no"] = race_no
    payload["race_time"] = time_to_minutes(deadline) or 0
    payload["eventDayLabel"] = race.get("eventDayLabel") or root.get("eventDayLabel") or root.get("seriesDay")
    payload["seriesDay"] = race.get("seriesDay") or root.get("seriesDay") or root.get("eventDayLabel")
    payload["eventDay"] = race.get("eventDay") or root.get("eventDay")

    payload["venue"] = (
        root.get("venue")
        or root.get("venueName")
        or "芦屋"
    )

    # -----------------------------
    # 天候・風・波
    # -----------------------------
    weather_value = live.get("weather")

    # direct内により詳細な値があれば優先
    direct = live.get("direct") or race.get("direct") or {}

    wind_direction = (
        live.get("wind_direction")
        or direct.get("wind_direction")
    )

    wind_speed = (
        live.get("wind_speed")
        or direct.get("wind_speed")
    )

    wave_height = (
        live.get("wave_height")
        or direct.get("wave_height")
    )

    air_temperature = (
        live.get("air_temperature")
        or direct.get("air_temperature")
    )

    water_temperature = (
        live.get("water_temperature")
        or direct.get("water_temperature")
    )

    weather_dict = {
        "weather": (
            weather_value
            if isinstance(weather_value, str)
            else direct.get("weather")
        ),
        "wind_direction": wind_direction,
        "wind_speed": wind_speed,
        "wave_height": wave_height,
        "air_temperature": air_temperature,
        "water_temperature": water_temperature,
    }

    payload["weather"] = {
        k: v
        for k, v in weather_dict.items()
        if v not in (None, "")
    }

    # FeatureBuilderのaliasが直接拾えるように
    if wind_direction not in (None, ""):
        payload["wind_direction"] = wind_direction

    if wind_speed not in (None, ""):
        payload["wind_speed"] = wind_speed
        payload["wind_speed_mps"] = wind_speed

    if wave_height not in (None, ""):
        payload["wave_height"] = wave_height
        payload["wave_cm"] = wave_height

    if air_temperature not in (None, ""):
        payload["air_temperature"] = air_temperature

    if water_temperature not in (None, ""):
        payload["water_temperature"] = water_temperature

    # -----------------------------
    # 潮
    # -----------------------------
    root_tide = (
        race.get("tide")
        or root.get("tide")
        or {}
    )

    payload["tide"] = build_tide_features(
        root_tide,
        deadline,
    )

    # -----------------------------
    # 艇データ
    # -----------------------------
    racers = deepcopy(
        payload.get("racers")
        or payload.get("entries")
        or []
    )

    if len(racers) != 6:
        raise ValueError(
            f"race {race_no}: six racers required"
        )

    # 直前情報の艇データを朝データへ上書き統合
    live_racers = direct.get("racers") or live.get("racers") or []

    merge_by_lane(
        racers,
        live_racers,
    )

    # -----------------------------
    # 実進入
    # -----------------------------
    actual_entry = (
        live.get("actual_entry")
        or direct.get("actual_entry")
    )

    actual_entry_valid = apply_actual_entry(
        racers,
        actual_entry,
    )

    # actual_course未設定時は通常進入
    for racer in racers:
        if racer.get("actual_course") in (None, ""):
            racer["actual_course"] = (
                racer.get("entry_course")
                or racer.get("lane")
            )

    payload["actual_entry"] = actual_entry if actual_entry_valid else list(range(1, 7))
    payload["entry_changed"] = any(
        int(racer.get("actual_course") or racer.get("lane")) != int(racer.get("lane"))
        for racer in racers
    )

    # -----------------------------
    # 展示
    # -----------------------------
    exhibition = live.get("exhibition") or {}
    exhibition_entries = (
        exhibition.get("entries")
        if isinstance(exhibition, dict)
        else []
    )

    merge_by_lane(
        racers,
        exhibition_entries,
        {
            "exhibition_time": "exhibition_time",
            "start_time": "start_timing",
            "start_raw": "start_raw",
            "exhibition_course": "exhibition_course",
            "exhibition_rank": "exhibition_rank",
            "exhibition_gap": "exhibition_gap",
            "start_rank": "start_rank",
            "tilt": "tilt",
        },
    )

    # -----------------------------
    # オリジナル展示
    # -----------------------------
    original = (
        live.get("original")
        or live.get("original_exhibition")
        or {}
    )

    original_entries = (
        original.get("entries")
        if isinstance(original, dict)
        else []
    )

    merge_by_lane(
        racers,
        original_entries,
        {
            "lap_time": "lap_time",
            "straight_time": "straight_time",
            "turn_time": "turn_time",
            "sum": "sum",
            "sum_difference": "sum_difference",
            "sum_exhibition": "sum_exhibition",
            "sum_lap": "sum_lap",
        },
    )

    # -----------------------------
    # 節間
    # -----------------------------
    setsukan = race.get("setsukan") or []

    if isinstance(setsukan, list):
        setsukan_by_lane = {}

        for item in setsukan:
            if not isinstance(item, dict):
                continue

            try:
                lane = int(item.get("lane") or 0)
            except (TypeError, ValueError):
                continue

            setsukan_by_lane[lane] = item

        for racer in racers:
            try:
                lane = int(racer.get("lane") or 0)
            except (TypeError, ValueError):
                continue

            item = setsukan_by_lane.get(lane)

            if not item:
                continue

            if item.get("season_runs") is not None:
                racer["season_runs"] = item["season_runs"]

            if item.get("season_groups") is not None:
                racer["season_groups"] = item["season_groups"]

    payload["racers"] = racers

    return payload


def main():
    p = argparse.ArgumentParser()

    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--models", required=True)
    p.add_argument("--player-db", required=True)

    p.add_argument("--race", type=int)

    p.add_argument(
        "--stage",
        default="pre",
        choices=["pre", "live", "final"],
    )

    a = p.parse_args()

    doc = json.loads(
        Path(a.input).read_text(
            encoding="utf-8"
        )
    )

    races = (
        doc.get("races")
        if isinstance(doc, dict)
        else None
    )

    if not isinstance(races, list):
        races = [doc]

    engine = AshiyaEngine(
        a.models,
        a.player_db,
    )

    results = []

    for race in races:
        no = int(
            race.get("race_no")
            or race.get("race")
            or 0
        )

        if a.race and no != a.race:
            continue

        merged = merge_race_payload(
            doc,
            race,
        )

        results.append(
            engine.predict(
                merged,
                stage=a.stage,
            )
        )

    out = {
        "engine": "ashiya_prediction_engine",
        "version": "1.6.1",
        "date": doc.get("date"),
        "venue": "ashiya",
        "predictions": results,
    }

    atomic_write_json(
        a.output,
        out,
    )

    print(
        json.dumps(
            {
                "output": a.output,
                "races": len(results),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
