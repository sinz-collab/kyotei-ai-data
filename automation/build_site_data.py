from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "venues.json"

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

        def block_value(base: int | None, offset: int, fallback: int) -> str:
            if base is not None and base + offset < len(block):
                return block[base + offset]
            return lines[index + fallback] if index + fallback < len(lines) else ""

        racers.append(
            {
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
                "season_runs": [],
                "season_groups": [],
            }
        )
    return racers if len(racers) == 6 else []


def event_day_info(lines: list[str]) -> tuple[int | str, str]:
    text = "\n".join(lines[:120])
    if "初日" in text:
        return 1, "初日"
    if "最終日" in text:
        return "", "最終日"
    match = re.search(r"([2-9])日目", text)
    return (int(match.group(1)), match.group(0)) if match else ("", "")


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
    merged = existing
    if not merged.get("tide") and morning.get("tide"):
        merged["tide"] = morning["tide"]
    for key in ("eventDay", "eventDayLabel", "eventScheduleLabels", "seriesDay"):
        if not merged.get(key) and morning.get(key):
            merged[key] = morning[key]
    return merged


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
    event_day: int | str = ""
    event_label = ""
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
            event_day, event_label = event_day_info(lines)
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
            payload = preserve_prediction_payload(payload, existing_path)
            if payload is None:
                is_open = False
                detail = {
                    **detail,
                    "reason": "prediction_payload_unavailable",
                    "predictionRequired": True,
                }
            else:
                payload = preserve_same_day_live_fields(payload, venue_dir / "latest.json")
                serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
                write_text_atomic(existing_path, serialized)
                write_text_atomic(venue_dir / "latest.json", serialized)
        statuses[slug] = {
            "open": is_open,
            "entryCount": 12 if is_open else 0,
            "firstDeadline": payload["races"][0]["deadline"] if is_open else "",
            "eventDay": payload.get("eventDay", "") if is_open else "",
            "eventDayLabel": payload.get("eventDayLabel", "") if is_open else "",
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
            item["predictionStatus"] = "ready" if state["open"] else (
                "unavailable" if reason == "prediction_payload_unavailable" else "not_running"
            )
        event_day = state.get("eventDay", "")
        if event_day != "":
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
