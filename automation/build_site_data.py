from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "venues.json"
PREDICTION_VENUES = {
    "toda",
    "wakamatsu",
    "shimonoseki",
    "heiwajima",
    "tokoname",
    "ashiya",
    "omura",
    "karatsu",
}

ALL_VENUES = [
    ("kiryu", "桐生"),
    ("toda", "戸田"),
    ("edogawa", "江戸川"),
    ("heiwajima", "平和島"),
    ("tamagawa", "多摩川"),
    ("hamanako", "浜名湖"),
    ("gamagori", "蒲郡"),
    ("tokoname", "常滑"),
    ("tsu", "津"),
    ("mikuni", "三国"),
    ("biwako", "びわこ"),
    ("suminoe", "住之江"),
    ("amagasaki", "尼崎"),
    ("naruto", "鳴門"),
    ("marugame", "丸亀"),
    ("kojima", "児島"),
    ("miyajima", "宮島"),
    ("tokuyama", "徳山"),
    ("shimonoseki", "下関"),
    ("wakamatsu", "若松"),
    ("ashiya", "芦屋"),
    ("fukuoka", "福岡"),
    ("karatsu", "唐津"),
    ("omura", "大村"),
]


def as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def normalize(scores: dict[int, float]) -> dict[str, float]:
    positive = {lane: max(0.001, score) for lane, score in scores.items()}
    total = sum(positive.values())
    return {str(lane): round(score * 100.0 / total, 2) for lane, score in positive.items()}


def clean_pct(value: str) -> str:
    return str(value or "").replace("%", "").strip()


def clean_count(value: str, prefix: str) -> str:
    text = str(value or "").strip()
    return text[len(prefix):] if text.startswith(prefix) else text


def entry_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]


def write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def parse_deadline(lines: list[str], race_no: int) -> str:
    text = "\n".join(lines)
    match = re.search(r"締切\s*([0-9]{1,2}:[0-9]{2})", text)
    if not match:
        match = re.search(rf"\b{race_no}R\s+([0-9]{{1,2}}:[0-9]{{2}})\b", text)
    return match.group(1) if match else ""


def parse_racers(lines: list[str]) -> list[dict]:
    season_labels = []
    try:
        season_start = lines.index("節間成績") + 1
    except ValueError:
        season_start = -1
    if season_start >= 0:
        for line in lines[season_start:season_start + 8]:
            if re.fullmatch(r"初日|[0-9]+日目|最終日", line):
                season_labels.append(line)
            elif line == "早見":
                break

    def parse_season(start: int, end: int) -> tuple[list[dict], list[dict], str]:
        tokens = lines[start:end]
        runs = []
        hayami = ""
        index = 0
        while index + 4 < len(tokens):
            if (
                re.fullmatch(r"[0-9]{1,2}R", tokens[index])
                and re.fullmatch(r"[1-6]", tokens[index + 1])
                and re.fullmatch(r"\.?[0-9]{1,2}", tokens[index + 2])
                and re.fullmatch(r"[1-6]", tokens[index + 3])
                and tokens[index + 4] == "着"
            ):
                runs.append(
                    {
                        "race": tokens[index],
                        "course": tokens[index + 1],
                        "entry_course": tokens[index + 1],
                        "st": tokens[index + 2],
                        "finish": f"{tokens[index + 3]}着",
                    }
                )
                index += 5
                continue
            if re.fullmatch(r"[0-9]{1,2}R", tokens[index]):
                hayami = tokens[index]
            index += 1
        groups = [
            {
                "day": season_labels[index // 2]
                if index // 2 < len(season_labels)
                else f"{index // 2 + 1}日目",
                "runs": runs[index:index + 2],
            }
            for index in range(0, len(runs), 2)
        ]
        return runs, groups, hayami

    starts = [
        index
        for index, line in enumerate(lines)
        if line in {"1", "2", "3", "4", "5", "6"}
        and index + 28 < len(lines)
        and re.fullmatch(r"[AB][12]", lines[index + 2])
        and lines[index + 4] == "歳"
        and lines[index + 6] == "kg"
    ]
    racers = []
    for position, index in enumerate(starts[:6]):
        lane = int(lines[index])
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        block = lines[index:end]
        number_positions = [i for i, value in enumerate(block) if value in {"No.", "No"}]
        motor_at = number_positions[0] if number_positions else None
        boat_at = number_positions[1] if len(number_positions) > 1 else None
        season_runs, season_groups, hayami = parse_season(index + 29, end)

        def block_value(base: int | None, offset: int, fallback: int) -> str:
            if base is not None and base + offset < len(block):
                return block[base + offset]
            return lines[index + fallback] if index + fallback < len(lines) else ""

        racer = {
                "lane": lane,
                "actual_course": lane,
                "entry_course": lane,
                "name": lines[index + 1],
                "class": lines[index + 2],
                "age": lines[index + 3],
                "weight": lines[index + 5],
                "branch": lines[index + 7],
                "f": clean_count(lines[index + 11], "F"),
                "l": clean_count(lines[index + 12], "L"),
                "avg_st": lines[index + 13],
                "local_st": lines[index + 14],
                "nat_win": lines[index + 15],
                "nat_2": clean_pct(lines[index + 16]),
                "nat_3": clean_pct(lines[index + 17]),
                "local_win": lines[index + 18],
                "local_2": clean_pct(lines[index + 19]),
                "local_3": clean_pct(lines[index + 20]),
                "motor_no": block_value(motor_at, 1, 22),
                "motor_2": clean_pct(block_value(motor_at, 2, 23)),
                "motor_3": clean_pct(block_value(motor_at, 3, 24)),
                "boat_no": block_value(boat_at, 1, 26),
                "boat_2": clean_pct(block_value(boat_at, 2, 27)),
                "boat_3": clean_pct(block_value(boat_at, 3, 28)),
                "season_runs": season_runs,
                "season_groups": season_groups,
            }
        if hayami:
            racer["hayami"] = hayami
        racers.append(racer)
    return racers if len(racers) == 6 else []


def event_day_info(lines: list[str], date: str) -> tuple[int | None, str | None]:
    target = datetime.strptime(date, "%Y-%m-%d")
    date_pattern = re.compile(
        rf"{target.month}月\s*{target.day}日\s*\([^)]*\)\s*(初日|[0-9]+日目|最終日)"
    )
    schedule = []
    current_label = None
    for line in lines:
        match = date_pattern.search(line)
        if match:
            current_label = match.group(1)
        schedule_match = re.search(
            r"\d{1,2}月\s*\d{1,2}日\s*\([^)]*\)\s*(初日|[0-9]+日目|最終日)",
            line,
        )
        if schedule_match:
            schedule.append(schedule_match.group(1))
    if current_label is None:
        return None, None
    if current_label == "初日":
        return 1, current_label
    match = re.fullmatch(r"([0-9]+)日目", current_label)
    if match:
        return int(match.group(1)), current_label
    if current_label == "最終日" and current_label in schedule:
        return schedule.index(current_label) + 1, current_label
    return None, current_label


def _has_value(value: object) -> bool:
    return value not in (None, "", [], {})


def merge_validated_morning_metadata(existing: dict, morning: dict) -> dict:
    """Merge only validated entry metadata; preserve prediction/live/result domains."""
    merged = existing
    existing_races = {
        int(race.get("race") or 0): race
        for race in merged.get("races") or []
        if int(race.get("race") or 0) in range(1, 13)
    }
    morning_races = {
        int(race.get("race") or 0): race
        for race in morning.get("races") or []
        if int(race.get("race") or 0) in range(1, 13)
    }
    candidate_day = morning.get("eventDay")
    season_evidence = sum(
        len(racer.get("season_runs") or [])
        for race in morning_races.values()
        for racer in race.get("racers") or []
    )
    current_day = merged.get("eventDay")
    day_valid = (
        isinstance(candidate_day, int)
        and candidate_day > 0
        and (
            (candidate_day == 1 and current_day in (None, "", 0, 1))
            or (candidate_day > 1 and season_evidence > 0)
        )
    )
    if day_valid:
        merged["eventDay"] = candidate_day
        merged["eventDayLabel"] = morning.get("eventDayLabel")
        merged["seriesDay"] = morning.get("seriesDay")
    if morning.get("tide") and not merged.get("tide"):
        merged["tide"] = morning["tide"]

    for race_no, existing_race in existing_races.items():
        incoming_race = morning_races.get(race_no)
        if not incoming_race:
            continue
        for key in ("deadline", "title", "type", "entry_changes"):
            if _has_value(incoming_race.get(key)):
                existing_race[key] = incoming_race[key]
        existing_by_lane = {
            int(racer.get("lane") or 0): racer
            for racer in existing_race.get("racers") or []
        }
        incoming_by_lane = {
            int(racer.get("lane") or 0): racer
            for racer in incoming_race.get("racers") or []
        }
        if sorted(existing_by_lane) != list(range(1, 7)) or sorted(incoming_by_lane) != list(range(1, 7)):
            continue
        for lane, incoming in incoming_by_lane.items():
            target = existing_by_lane[lane]
            if target.get("name") and incoming.get("name") and target["name"] != incoming["name"]:
                continue
            for key, value in incoming.items():
                if key in {"season_runs", "season_groups"}:
                    if value:
                        target[key] = value
                elif _has_value(value):
                    target[key] = value
        if day_valid:
            existing_race["eventDay"] = merged["eventDay"]
            existing_race["eventDayLabel"] = merged["eventDayLabel"]
    return merged


def prediction_payload_is_complete(payload: dict, expected_date: str) -> bool:
    if payload.get("date") != expected_date:
        return False
    predictions = payload.get("preds")
    if not isinstance(predictions, dict) or len(predictions) != 12:
        return False
    if payload.get("engine") == "deterministic_baseline_v1":
        return False
    for race_no in range(1, 13):
        prediction = predictions.get(str(race_no))
        if not isinstance(prediction, dict):
            return False
        for key in ("win", "second", "third"):
            values = prediction.get(key)
            if not isinstance(values, dict) or len(values) != 6:
                return False
        if not prediction.get("sab"):
            return False
        if not any(
            isinstance(prediction.get(key), list) and prediction[key]
            for key in ("ai", "aiUpset", "balance", "tickets")
        ):
            return False
    return True


def preserve_prediction_payload(morning: dict, existing_path: Path) -> dict | None:
    if not existing_path.is_file():
        return None
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not prediction_payload_is_complete(existing, morning.get("date", "")):
        return None

    # The scheduled collector has no venue engines. It may enrich non-prediction
    # metadata, but it must never replace engine output, tickets, live data, or
    # confirmed results with a generic baseline.
    return merge_validated_morning_metadata(existing, morning)


def preserve_same_day_live_fields(payload: dict, existing_path: Path) -> dict:
    if not existing_path.is_file():
        return payload
    try:
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return payload
    if existing.get("date") != payload.get("date"):
        return payload
    previous_predictions = existing.get("preds") or {}
    for race_no, prediction in (payload.get("preds") or {}).items():
        previous = previous_predictions.get(race_no) or {}
        realtime = previous.get("realtime")
        odds = previous.get("odds")
        result = previous.get("result")
        realtime_has_data = isinstance(realtime, dict) and any(
            value not in (None, "", [], {}) for value in realtime.values()
        )
        odds_has_data = isinstance(odds, dict) and bool(odds)
        if realtime_has_data:
            prediction["realtime"] = realtime
        if odds_has_data:
            prediction["odds"] = odds
        if isinstance(result, dict) and result.get("status") == "ok":
            prediction["result"] = result
        if (realtime_has_data or odds_has_data) and previous.get("predictionStage"):
            prediction["predictionStage"] = previous["predictionStage"]
    return payload


def build_payload(venue: dict, date: str, source_dir: Path) -> tuple[dict | None, dict]:
    races = []
    predictions = {}
    event_day: int | None = None
    event_label: str | None = None
    for race_no in range(1, 13):
        entry_path = source_dir / "races" / f"race_{race_no:02d}_entry.txt"
        if not entry_path.exists():
            return None, {"reason": f"missing_entry_{race_no:02d}"}
        lines = entry_lines(entry_path)
        racers = parse_racers(lines)
        deadline = parse_deadline(lines, race_no)
        if len(racers) != 6 or not deadline:
            return None, {"reason": f"invalid_entry_{race_no:02d}", "racers": len(racers)}
        if race_no == 1:
            event_day, event_label = event_day_info(lines, date)
        races.append(
            {
                "race": race_no,
                "deadline": deadline,
                "title": venue["name"],
                "type": "",
                "racers": racers,
                "entry_changes": [],
                "eventDayLabel": event_label,
                "eventDay": event_day,
            }
        )
    tide_path = source_dir / "tide_today.json"
    tide = {}
    if tide_path.exists():
        candidate = json.loads(tide_path.read_text(encoding="utf-8"))
        if candidate.get("date") == date:
            tide = candidate
    return (
        {
            "venue": venue["name"],
            "date": date,
            "engine": "",
            "seriesDay": event_label,
            "races": races,
            "preds": predictions,
            "tide": tide,
            "eventDayLabel": event_label,
            "eventDay": event_day,
            "eventScheduleLabels": {},
        },
        {"reason": "ok"},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--source-root", default="work/races")
    parser.add_argument("--data-root", default="data")
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    source_root = Path(args.source_root)
    data_root = Path(args.data_root)
    date_dir = args.date.replace("-", "")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    configured = {venue["slug"]: venue for venue in config["venues"]}
    statuses = {}
    expected_status_paths = {
        source_root / venue["name"] / date_dir / "fetch_status.json"
        for venue in configured.values()
    }
    actual_status_paths = set(source_root.glob(f"*/{date_dir}/fetch_status.json"))
    if actual_status_paths != expected_status_paths:
        missing = sorted(str(path) for path in expected_status_paths - actual_status_paths)
        unexpected = sorted(str(path) for path in actual_status_paths - expected_status_paths)
        raise RuntimeError(f"fetch status mismatch: missing={missing} unexpected={unexpected}")

    for slug, venue in configured.items():
        source_dir = source_root / venue["name"] / date_dir
        status_path = source_dir / "fetch_status.json"
        fetch_status = json.loads(status_path.read_text(encoding="utf-8"))
        if (
            fetch_status.get("date") != args.date
            or fetch_status.get("slug") != slug
            or fetch_status.get("name") != venue["name"]
        ):
            raise RuntimeError(
                f"fetch status identity mismatch: expected={slug}/{venue['name']}/{args.date} "
                f"actual={fetch_status.get('slug')}/{fetch_status.get('name')}/{fetch_status.get('date')}"
            )
        payload = None
        detail = {
            "reason": fetch_status.get("precheck", {}).get("reason", "fetch_incomplete"),
            "fetchReturnCode": fetch_status.get("fetchReturnCode"),
            "fetchAttempts": fetch_status.get("fetchAttempts", []),
            "tide": fetch_status.get("tide", {}),
        }
        if fetch_status.get("open") and fetch_status.get("entryCount") == 12:
            payload, detail = build_payload(venue, args.date, source_dir)
        is_open = payload is not None
        if is_open:
            venue_dir = data_root / "venues" / slug
            venue_dir.mkdir(parents=True, exist_ok=True)
            existing_path = venue_dir / f"{date_dir}.json"
            if slug in PREDICTION_VENUES:
                payload = preserve_prediction_payload(payload, existing_path)
                if payload is None:
                    is_open = False
                    detail = {
                        **detail,
                        "reason": "prediction_payload_unavailable",
                        "predictionRequired": True,
                    }
            else:
                payload["engine"] = None
                payload["preds"] = {}
                detail = {**detail, "reason": "venue_engine_not_registered"}
            if is_open:
                payload = preserve_same_day_live_fields(payload, venue_dir / "latest.json")
                serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                write_text_atomic(existing_path, serialized)
                write_text_atomic(venue_dir / "latest.json", serialized)
        statuses[slug] = {
            "open": is_open,
            "entryCount": 12 if is_open else 0,
            "firstDeadline": payload["races"][0]["deadline"] if is_open else "",
            "eventDay": payload.get("eventDay") if is_open else None,
            "eventDayLabel": payload.get("eventDayLabel") if is_open else None,
            "detail": detail,
        }

    manifest_venues = []
    for slug, name in ALL_VENUES:
        state = statuses.get(slug, {"open": False, "entryCount": 0, "firstDeadline": ""})
        item = {
            "slug": slug,
            "name": name,
            "open": state["open"],
            "entryCount": state["entryCount"],
            "firstDeadline": state["firstDeadline"],
            "date": args.date,
            "dateDir": date_dir,
            "dataPath": f"venues/{slug}/{date_dir}.json" if state["open"] else "",
            "latestPath": f"venues/{slug}/latest.json" if state["open"] else "",
        }
        reason = state.get("detail", {}).get("reason", "")
        if reason:
            item["availabilityReason"] = reason
        if slug in configured:
            if slug not in PREDICTION_VENUES and state["open"]:
                item["predictionStatus"] = "unavailable"
            else:
                item["predictionStatus"] = "ready" if state["open"] else (
                    "unavailable" if reason == "prediction_payload_unavailable" else "not_running"
                )
        event_day = state.get("eventDay")
        if event_day is not None:
            item["eventDay"] = event_day
        if state.get("eventDayLabel"):
            item["eventDayLabel"] = state["eventDayLabel"]
        manifest_venues.append(item)

    now = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    manifest = {
        "version": 1,
        "updatedAt": now,
        "date": args.date,
        "dateDir": date_dir,
        "venues": manifest_venues,
    }
    data_root.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        data_root / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    report = {"date": args.date, "createdAt": now, "venues": statuses}
    write_text_atomic(
        data_root / "morning_report.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
