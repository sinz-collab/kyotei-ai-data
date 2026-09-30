from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "heiwajima_v1"
MASTER_DB = ENGINE_DIR / "master_db" / "heiwajima_runtime_master.sqlite"
ENGINE_ID = "heiwajima_complete_v2_7_20260930"
MASTER_ID = "heiwajima_runtime_master_v1_20260728"
V2_7_START_DATE = "2026-09-30"
LANES = (1, 2, 3, 4, 5, 6)


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "" or value == "-":
        return default
    try:
        parsed = float(re.sub(r"[^0-9.\-]", "", str(value)))
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_name(value: Any) -> str:
    # Source masters use full-width and repeated spaces. Matching must ignore all spaces.
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def build_player_index(db_path: Path = MASTER_DB) -> dict[str, str]:
    if not db_path.exists():
        raise FileNotFoundError(f"master_db_missing: {db_path}")
    candidates: dict[str, set[str]] = {}
    with sqlite3.connect(db_path) as connection:
        for table, id_col in (
            ("player_local_stats", "reg_no"),
            ("player_course_stats", "player_id"),
        ):
            rows = connection.execute(
                f"SELECT {id_col}, player_name FROM {table} "
                f"WHERE {id_col} IS NOT NULL AND player_name IS NOT NULL"
            ).fetchall()
            for player_id, player_name in rows:
                key = normalize_name(player_name)
                if not key:
                    continue
                try:
                    normalized_id = str(int(float(player_id)))
                except (TypeError, ValueError):
                    continue
                candidates.setdefault(key, set()).add(normalized_id)
    # Ambiguous names are deliberately excluded instead of silently choosing a player.
    return {name: next(iter(ids)) for name, ids in candidates.items() if len(ids) == 1}


def validate_payload(payload: dict, target_date: str) -> None:
    if payload.get("venueId") != "heiwajima" or payload.get("venue") != "平和島":
        raise RuntimeError("not_heiwajima_payload")
    if payload.get("date") != target_date:
        raise RuntimeError(
            f"date_mismatch: expected={target_date} actual={payload.get('date')}"
        )
    races = payload.get("races")
    if not isinstance(races, list) or len(races) != 12:
        raise RuntimeError("heiwajima_races_must_be_12")
    race_numbers = sorted(int(race.get("race") or 0) for race in races)
    if race_numbers != list(range(1, 13)):
        raise RuntimeError(f"invalid_race_numbers: {race_numbers}")
    for race in races:
        racers = race.get("racers")
        if not isinstance(racers, list) or len(racers) != 6:
            raise RuntimeError(f"race_{race.get('race')}_racers_must_be_6")
        lanes = sorted(int(racer.get("lane") or 0) for racer in racers)
        if lanes != list(LANES):
            raise RuntimeError(f"race_{race.get('race')}_invalid_lanes: {lanes}")


def finish_number(value: Any) -> int | None:
    match = re.search(r"[1-6]", str(value or ""))
    return int(match.group()) if match else None


def season_form_score(racer: dict) -> float:
    runs = racer.get("season_runs") or []
    if not isinstance(runs, list) or not runs:
        return 0.0
    weighted_total = 0.0
    weight_total = 0.0
    for index, run in enumerate(runs[-10:]):
        weight = 1.0 + index * 0.08
        finish = finish_number(run.get("finish"))
        finish_score = {1: 2.2, 2: 1.3, 3: 0.7, 4: 0.0, 5: -0.7, 6: -1.2}.get(finish, 0.0)
        st = number(run.get("st"), 0.18)
        st_score = clamp((0.18 - st) * 8.0, -0.8, 0.8)
        weighted_total += weight * (finish_score + st_score)
        weight_total += weight
    return round(clamp(weighted_total / max(weight_total, 1.0), -3.0, 3.0), 4)


def motor_power_score(racer: dict) -> float:
    motor2 = number(racer.get("motor_2"), 33.0)
    motor3 = number(racer.get("motor_3"), 50.0)
    boat2 = number(racer.get("boat_2"), 33.0)
    boat3 = number(racer.get("boat_3"), 50.0)
    score = (
        (motor2 - 33.0) / 8.0 * 0.42
        + (motor3 - 50.0) / 12.0 * 0.28
        + (boat2 - 33.0) / 9.0 * 0.18
        + (boat3 - 50.0) / 13.0 * 0.12
    )
    return round(clamp(score, -3.0, 3.0), 4)


def tide_context(payload: dict, race: dict) -> dict:
    tide = race.get("tide") or payload.get("tide") or {}
    context = {
        "tide_type_est": tide.get("tide_type_est") or tide.get("tideType") or tide.get("tide_type"),
        "tide_direction": tide.get("tide_direction") or tide.get("direction") or tide.get("phase"),
        "tide_phase": race.get("tide_phase") or tide.get("tide_phase") or tide.get("phase"),
        "tide_window": race.get("tide_window") or tide.get("tide_window") or tide.get("nearest"),
        "tide_level_band": race.get("tide_level_band") or tide.get("tide_level_band") or tide.get("band"),
        "tide_cm_est": race.get("tide_cm_est") or tide.get("tide_cm_est") or tide.get("level"),
    }
    events = []
    for event in tide.get("events") or []:
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", str(event.get("time") or ""))
        if not match:
            continue
        events.append({
            "minute": int(match.group(1)) * 60 + int(match.group(2)),
            "level": number(event.get("level"), float("nan")),
        })
    deadline = re.fullmatch(r"(\d{1,2}):(\d{2})", str(race.get("deadline") or ""))
    if deadline and len(events) >= 2:
        race_minute = int(deadline.group(1)) * 60 + int(deadline.group(2))
        events.sort(key=lambda item: item["minute"])
        low_events = []
        for index, event in enumerate(events):
            previous = events[index - 1]["level"] if index > 0 else float("inf")
            following = events[index + 1]["level"] if index + 1 < len(events) else float("inf")
            if event["level"] <= previous and event["level"] <= following:
                low_events.append(event)
        upcoming = [event for event in low_events if event["minute"] >= race_minute]
        if upcoming:
            context["minutes_to_low_tide"] = upcoming[0]["minute"] - race_minute
        before = [event for event in events if event["minute"] <= race_minute]
        after = [event for event in events if event["minute"] >= race_minute]
        if before and after:
            left, right = before[-1], after[0]
            span = max(1, right["minute"] - left["minute"])
            ratio = (race_minute - left["minute"]) / span
            estimated_level = left["level"] + (right["level"] - left["level"]) * ratio
            context["tide_cm_est"] = round(estimated_level, 1)
            context["low_water_band"] = estimated_level < 100.0
            if not context.get("tide_level_band"):
                context["tide_level_band"] = "low" if estimated_level < 100.0 else "normal"
    return context


def weather_context(payload: dict, race: dict) -> dict:
    weather = race.get("weather") or (race.get("live") or {}).get("weather") or payload.get("weather") or {}
    return {
        "weather": weather.get("weather"),
        "wind_direction": weather.get("wind_direction") or weather.get("windDirection"),
        "wind_speed": weather.get("wind_speed") or weather.get("wind") or weather.get("windSpeed"),
        "wave_height": weather.get("wave_height") or weather.get("wave") or weather.get("waveHeight"),
        "water_temperature": weather.get("water_temperature") or weather.get("water"),
    }


def read_complete_live_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("status") != "complete" or payload.get("complete") is not True:
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else {}


def exhibition_live_context(data_root: Path, target_date: str, race_no: int) -> dict:
    live_dir = data_root / "live" / target_date / "heiwajima" / f"{race_no:02d}"
    exhibition = read_complete_live_json(live_dir / "exhibition.json")
    if not exhibition:
        return {}

    entries = exhibition.get("entries") or []
    if len(entries) != 6:
        return {}

    by_lane = {int(row.get("lane") or 0): row for row in entries}
    if sorted(by_lane) != list(LANES):
        return {}

    exhibition_st = {
        str(lane): number(by_lane[lane].get("start_time"), 0.99)
        for lane in LANES
    }
    exhibition_time_rank = {
        str(lane): int(number(by_lane[lane].get("exhibition_rank"), 99))
        for lane in LANES
    }

    ordered_st = sorted(exhibition_st.values())
    second_fastest = ordered_st[1]
    median_st = (ordered_st[2] + ordered_st[3]) / 2.0
    slit = {}
    for lane in LANES:
        st = exhibition_st[str(lane)]
        if st <= second_fastest + 0.02:
            slit[str(lane)] = "advance"
        elif st >= median_st + 0.08:
            slit[str(lane)] = "dent"
        else:
            slit[str(lane)] = "neutral"

    actual_courses = {
        str(lane): int(number(by_lane[lane].get("exhibition_course"), lane))
        for lane in LANES
    }

    return {
        "slit": slit,
        "exhibition_st": exhibition_st,
        "exhibition_time_rank": exhibition_time_rank,
        "actual_courses": actual_courses,
        "source": "data/live/.../exhibition.json",
        "fetched": True,
    }


def engine_input_for(
    payload: dict,
    race: dict,
    player_index: dict[str, str],
    *,
    stage: str = "pre",
    live_context: dict | None = None,
) -> tuple[dict, list[str]]:
    missing_codes: list[str] = []
    boats = []
    for racer in race["racers"]:
        lane = int(racer["lane"])
        reg_no = racer.get("reg_no") or racer.get("registration_no")
        if reg_no is None:
            reg_no = player_index.get(normalize_name(racer.get("name")))
        if reg_no is None:
            # Keep prediction operational, but prevent accidental master match.
            reg_no = f"unresolved:{normalize_name(racer.get('name')) or lane}"
            missing_codes.append("player_id_unresolved")
        live_context = live_context or {}
        actual_courses = live_context.get("actual_courses") or {}
        actual_course = int(
            actual_courses.get(str(lane))
            or actual_courses.get(lane)
            or racer.get("actual_course")
            or racer.get("entry_course")
            or racer.get("course")
            or lane
        )
        boats.append(
            {
                "boat_no": lane,
                "reg_no": str(reg_no),
                "player_name": racer.get("name") or "",
                "actual_course": actual_course,
                "class": racer.get("class") or "B1",
                "avg_st": racer.get("avg_st"),
                "local_st": racer.get("local_st"),
                "nat_win_score": racer.get("nat_win"),
                "local_win_score": racer.get("local_win"),
                "local_2_rate": racer.get("local_2"),
                "local_3_rate": racer.get("local_3"),
                "motor_recent": racer.get("motor_recent") or {},
                "season_runs": racer.get("season_runs") or [],
                "kimarite": {
                    "starts": racer.get("boaters_kimarite_starts"),
                    "escape_rate": racer.get("boaters_escape_rate"),
                    "sashare_rate": racer.get("boaters_sashare_rate"),
                    "makurare_rate": racer.get("boaters_makurare_rate"),
                    "makurare_zashi_rate": racer.get("boaters_makurare_zashi_rate"),
                    "nigashi_rate": racer.get("boaters_nigashi_rate"),
                    "sashi_rate": racer.get("boaters_sashi_rate"),
                    "makuri_rate": racer.get("boaters_makuri_rate"),
                    "makuri_sashi_rate": racer.get("boaters_makuri_sashi_rate"),
                },
                "motor": {
                    "power_score": motor_power_score(racer),
                    "motor_2": racer.get("motor_2"),
                    "motor_3": racer.get("motor_3"),
                    "boat_2": racer.get("boat_2"),
                    "boat_3": racer.get("boat_3"),
                },
                "season": {"form_score": season_form_score(racer)},
            }
        )
    return (
        {
            "race_date": payload["date"],
            "race_no": int(race["race"]),
            "stage": stage,
            "boats": boats,
            "tide": tide_context(payload, race),
            "weather": weather_context(payload, race),
            "live": live_context or {},
            "event_day_no": (
                race.get("eventDay")
                or (race.get("race_meta") or {}).get("day_no")
                or payload.get("eventDay")
                or payload.get("seriesDay")
            ),
        },
        sorted(set(missing_codes)),
    )


def percent_map(result: dict, key: str) -> dict[str, float]:
    values = {
        str(int(row["boat_no"])): round(float(row[key]) * 100.0, 1)
        for row in result["probabilities"]
    }

    # Win/second/third are mutually exclusive positions and each total 100%.
    # Top3 is a per-boat inclusion probability, so its six-boat total is not 100%.
    if key != "top3_prob":
        correction = round(100.0 - sum(values.values()), 1)
        largest = max(values, key=values.get)
        values[largest] = round(values[largest] + correction, 1)

    return values


def site_ticket(ticket: dict) -> dict:
    output = {
        "combo": ticket["combination"],
        "role": {
            "main": "本線",
            "deviation": "ズレ対応",
            "upset": "荒れ対応",
        }.get(ticket.get("type"), ticket.get("type") or ""),
        "prob": round(float(ticket.get("share") or 0.0) * 100.0, 1),
        "odds": "-",
        "score": round(float(ticket.get("score") or 0.0), 8),
    }
    if ticket.get("s16_dedicated"):
        output["s16Dedicated"] = True
    if "predicted_probability" in ticket:
        output["predictedProbability"] = round(float(ticket["predicted_probability"]) * 100.0, 3)
    return output


def site_prediction(result: dict, connector_missing: list[str]) -> dict:
    rows = result["probabilities"]
    win = percent_map(result, "win_prob")
    second = percent_map(result, "second_prob")
    third = percent_map(result, "third_prob")
    top3 = percent_map(result, "top3_prob")
    ranked = sorted(rows, key=lambda row: float(row["win_prob"]), reverse=True)
    axis_lane = int(ranked[0]["boat_no"])
    second_lane = int(ranked[1]["boat_no"])
    axis_gap = round((float(ranked[0]["win_prob"]) - float(ranked[1]["win_prob"])) * 100.0, 1)
    scenarios = result.get("scenarios") or []
    primary_scenario = scenarios[0] if scenarios else {}
    all_tickets = [site_ticket(ticket) for ticket in result.get("tickets") or []]
    ai = [ticket for ticket in all_tickets if ticket["role"] in ("本線", "ズレ対応")]
    ai_upset = [ticket for ticket in all_tickets if ticket["role"] == "荒れ対応"]
    if not ai:
        ai = all_tickets[:6]
    # 荒れ対応が生成されていない場合、本線やズレ対応を重複表示しない。
    if not ai_upset:
        ai_upset = []

    completeness = result.get("data_completeness") or {}
    missing_codes = sorted(set((completeness.get("missing_codes") or []) + connector_missing))
    sab_data = result.get("sab") or {}
    confidence = round(float(sab_data.get("confidence") or 0.0), 1)
    sab = str(sab_data.get("grade") or "B")
    upset_index = round(
        clamp(
            100.0 - float(win.get("1", 0.0))
            + sum(float(win.get(str(lane), 0.0)) for lane in (4, 5, 6)) * 0.35,
            0.0,
            100.0,
        ),
        1,
    )
    return {
        "engine": ENGINE_ID,
        "win": win,
        "second": second,
        "third": third,
        "top3": top3,
        "sab": sab,
        "confidence": confidence,
        "upsetIndex": upset_index,
        "attack": {
            "attackLane": axis_lane,
            "scenario": primary_scenario.get("name"),
            "scenarioProbability": round(float(primary_scenario.get("probability") or 0.0) * 100.0, 1),
        },
        "readability": {
            "axisLane": axis_lane,
            "secondHeadLane": second_lane,
            "axisGap": axis_gap,
            "topScenarioProbability": round(
                float(sab_data.get("top_scenario_probability") or 0.0),
                4,
            ),
            "masterCoverage": round(
                float(sab_data.get("master_coverage") or 0.0),
                4,
            ),
            "entryChanged": bool(
                sab_data.get("entry_changed", False)
            ),
            "ticketCountUsed": bool(
                sab_data.get("ticket_count_used", False)
            ),
            "comment": f"主シナリオ: {primary_scenario.get('name') or '未確定'} / 軸差 {axis_gap:.1f}pt",
        },
        "ai": ai,
        "aiUpset": ai_upset,
        "tickets": all_tickets,
        "ticketCount": len(all_tickets),
        "coverageNeed": result.get("coverageNeed") or {},
        "s16": result.get("s16") or {
            "activated": False, "level": "off", "signalCount": 0, "signals": {}
        },
        "s16DedicatedTicket": result.get("s16DedicatedTicket"),
        "conditionalPlacement": result.get("conditionalPlacement") or {},
        "scenarios": scenarios,
        "headExclusionLog": result.get("head_exclusion_log") or [],
        "sourceSummary": {
            "masterDbLoaded": completeness.get("master_db_loaded") is True,
            "playerCourseReflected": completeness.get("player_course_reflected", 0),
            "localStReflected": completeness.get("local_st_reflected", 0),
            "entryChanged": completeness.get("entry_changed") is True,
            "missingCodes": missing_codes,
            "oddsUsedForProbability": False,
            "exhibitionStartUsedAlone": False,
        },
        "missingCodes": missing_codes,
        "dataCoverage": completeness,
        "logs": [
            {
                "stage": "master_connection",
                "reason": (
                    f"選手×コース {completeness.get('player_course_reflected', 0)}/6艇、"
                    f"当地ST {completeness.get('local_st_reflected', 0)}/6艇を反映。"
                ),
            },
            {
                "stage": "scenario",
                "reason": f"主シナリオは {primary_scenario.get('name') or '未確定'}。",
            },
            {
                "stage": "missing_data",
                "reason": "欠損なし" if not missing_codes else ", ".join(missing_codes),
            },
        ],
        "probabilityFlow": {
            "required": True,
            "baseApplied": True,
            "baseLabel": "平和島v1事前エンジン予想",
            "realtimeApplied": False,
            "realtimeLabel": "展示・実進入・直前情報待ち",
            "reviewed": False,
            "reviewLabel": "再精査後の調整数字",
            "adjustedRequired": True,
        },
        "predictionStage": {
            "label": "仮予想",
            "statusText": "前データでのエンジン予想。直前情報取得後にサーバー側で再精査",
            "badge": "仮予想",
            "color": "yellow",
        },
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
        "accuracyLog": {
            "engine": ENGINE_ID,
            "sab": sab,
            "coverageNeed": result.get("coverageNeed") or {},
            "ticketCount": len(all_tickets),
            "s16": result.get("s16") or {},
            "s16DedicatedTicket": result.get("s16DedicatedTicket"),
            "probabilities": {"p1": win, "p2": second, "p3": third},
            "tickets": [ticket["combo"] for ticket in all_tickets],
            "resultAvailable": False,
            "hit": None,
        },
    }


def prediction_complete(prediction: dict) -> bool:
    required = ("win", "second", "third", "sab", "ai", "aiUpset", "sourceSummary")
    if not all(key in prediction for key in required):
        return False
    for key in ("win", "second", "third"):
        values = prediction.get(key)
        if not isinstance(values, dict) or sorted(map(int, values.keys())) != list(LANES):
            return False
        if abs(sum(float(value) for value in values.values()) - 100.0) > 0.2:
            return False
    return True


def result_combination(race: dict) -> str | None:
    result = race.get("result") or {}
    direct = result.get("trifecta") or result.get("combination")
    if re.fullmatch(r"[1-6]-[1-6]-[1-6]", str(direct or "")):
        return str(direct)
    order = result.get("order") or result.get("finish_order") or []
    if isinstance(order, list) and len(order) >= 3:
        combo = "-".join(str(value) for value in order[:3])
        if re.fullmatch(r"[1-6]-[1-6]-[1-6]", combo):
            return combo
    return None


def update_accuracy_only(prediction: dict, race: dict) -> dict:
    frozen = deepcopy(prediction)
    combo = result_combination(race)
    accuracy = deepcopy(frozen.get("accuracyLog") or {})
    tickets = accuracy.get("tickets") or [
        ticket.get("combo")
        for ticket in (frozen.get("tickets") or frozen.get("ai") or []) + (frozen.get("aiUpset") or [])
        if ticket.get("combo")
    ]
    accuracy.update({
        "engine": frozen.get("engine") or ENGINE_ID,
        "ticketCount": len(set(tickets)),
        "tickets": list(dict.fromkeys(tickets)),
        "resultAvailable": combo is not None,
        "result": combo,
        "hit": combo in tickets if combo else None,
    })
    frozen["accuracyLog"] = accuracy
    return frozen


def apply_heiwajima_v1(payload: dict, target_date: str, data_root: Path) -> dict:
    if target_date < V2_7_START_DATE:
        raise RuntimeError(f"v2_7_historical_date_protected: {target_date}")
    validate_payload(payload, target_date)
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))
    from heiwajima_prediction_engine import calculate

    player_index = build_player_index()
    existing_predictions = deepcopy(payload.get("preds") or {})
    predictions: dict[str, dict] = {}
    failures = []
    for race in payload["races"]:
        race_no = int(race["race"])
        try:
            existing_prediction = existing_predictions.get(str(race_no)) or {}
            prediction_locked = bool(existing_prediction.get("liveApplied")) or result_combination(race) is not None
            if (
                existing_prediction.get("engine") == ENGINE_ID
                and prediction_complete(existing_prediction)
                and prediction_locked
            ):
                predictions[str(race_no)] = update_accuracy_only(existing_prediction, race)
                continue
            live_context = exhibition_live_context(data_root, target_date, race_no)
            stage = "final" if live_context else "pre"
            engine_input, connector_missing = engine_input_for(
                payload,
                race,
                player_index,
                stage=stage,
                live_context=live_context,
            )
            result = calculate(engine_input)
            prediction = site_prediction(result, connector_missing)
            if live_context:
                prediction["probabilityFlow"]["realtimeApplied"] = True
                prediction["probabilityFlow"]["realtimeLabel"] = "直前展示・スリット・実進入を反映"
                prediction["predictionStage"] = {
                    "label": "最終予想",
                    "statusText": "直前展示・スリット・実進入を反映した再計算",
                    "badge": "最終予想",
                    "color": "green",
                }
                prediction["liveApplied"] = True
                prediction["liveSource"] = live_context.get("source")
                race["live"] = live_context
            else:
                prediction["liveApplied"] = False
            if not prediction_complete(prediction):
                raise RuntimeError("prediction_output_incomplete")
            for preserved_key in ("odds", "result", "realtime", "prediction_history", "active_prediction_stage"):
                if preserved_key in existing_prediction:
                    prediction[preserved_key] = deepcopy(existing_prediction[preserved_key])
            predictions[str(race_no)] = prediction
        except Exception as exc:  # One incomplete race must fail the full venue publication.
            failures.append({"race": race_no, "error": f"{type(exc).__name__}: {exc}"})
    if failures:
        raise RuntimeError(
            "heiwajima_v1_generation_failed: " + json.dumps(failures, ensure_ascii=False)
        )
    if sorted(map(int, predictions.keys())) != list(range(1, 13)):
        raise RuntimeError("heiwajima_v1_predictions_must_be_12")

    payload["engine"] = ENGINE_ID
    payload["preds"] = predictions
    payload["predictionEngine"] = {
        "id": ENGINE_ID,
        "master": MASTER_ID,
        "generatedBy": "automation/apply_heiwajima_v1.py",
        "oddsUsedForProbability": False,
        "exhibitionStartUsedAlone": False,
        "actualEntryReanalysis": True,
        "conditionalPlacement": True,
        "coverageRuleVersion": "heiwajima_coverage_v1",
        "s16OuterHead": True,
        "sabTicketCountUsed": False,
        "raceCount": 12,
    }
    return payload


def update_manifest(data_root: Path, payload: dict, dated_path: Path) -> None:
    manifest_path = data_root / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed = False
    for venue in manifest.get("venues") or []:
        if venue.get("slug") != "heiwajima":
            continue
        venue["predictionAvailable"] = True
        venue["prediction_available"] = True
        venue["predictionStatus"] = "ready"
        venue["prediction_status"] = "ready"
        venue["engine"] = ENGINE_ID
        venue["dataPath"] = str(dated_path.relative_to(data_root)).replace("\\", "/")
        changed = True
    if changed:
        atomic_write_json(manifest_path, manifest)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--require-open", action="store_true")
    args = parser.parse_args()

    date_dir = args.date.replace("-", "")
    data_root = Path(args.data_root)
    if args.date < V2_7_START_DATE:
        print(f"Heiwajima v2.7 historical date is protected: {args.date}")
        return 0
    dated_path = data_root / "venues" / "heiwajima" / f"{date_dir}.json"
    latest_path = data_root / "venues" / "heiwajima" / "latest.json"
    if not dated_path.exists():
        message = f"Heiwajima data is not open: {dated_path}"
        if args.require_open:
            raise FileNotFoundError(message)
        print(message)
        return 0

    payload = json.loads(dated_path.read_text(encoding="utf-8"))
    payload = apply_heiwajima_v1(payload, args.date, data_root)
    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)
    update_manifest(data_root, payload, dated_path)

    print(
        json.dumps(
            {
                "date": args.date,
                "engine": payload["engine"],
                "raceCount": len(payload["preds"]),
                "datedPath": str(dated_path),
                "latestPath": str(latest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
