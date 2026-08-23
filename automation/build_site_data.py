from __future__ import annotations

import argparse
import json
import math
import re
import unicodedata
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "venues.json"
PREDICTION_VENUES = {
    "toda",
    "wakamatsu",
    "heiwajima",
    "tokoname",
    "ashiya",
    "omura",
    "karatsu",
    "biwako",
    "shimonoseki",
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


def parse_race_type(lines: list[str]) -> str:
    """Return the current race type displayed in saved BOATERS ENTRY text."""
    deadline_re = re.compile(r"^締切\s*[0-9]{1,2}:[0-9]{2}$")
    for index, line in enumerate(lines):
        if not deadline_re.fullmatch(line.strip()):
            continue
        for candidate in lines[index + 1:]:
            race_type = candidate.strip()
            if race_type:
                return race_type
        break
    return ""


def parse_entry_fixed(lines: list[str]) -> bool:
    """Return whether BOATERS marks the current race as fixed entry."""
    deadline_re = re.compile(r"^締切\s*[0-9]{1,2}:[0-9]{2}$")
    for index, line in enumerate(lines):
        if not deadline_re.fullmatch(line.strip()):
            continue
        for candidate in lines[index + 1:]:
            text = candidate.strip()
            if text == "出走表":
                break
            if "進入固定" in text:
                return True
        return False
    return False


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

        # BOATERS may omit the second ST field. Infer the performance block
        # from the first motor "No." instead of relying on fixed offsets.
        stats_start = motor_at - 6 if motor_at is not None else 15
        st_values = block[13:stats_start] if stats_start >= 13 else []
        avg_st = st_values[0] if st_values else "-"
        local_st = st_values[1] if len(st_values) > 1 else "-"

        # Meeting form starts immediately after the boat number and 2/3 rates.
        season_start = index + (boat_at + 4 if boat_at is not None else 29)
        season_runs, season_groups, hayami = parse_season(season_start, end)

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
                "avg_st": avg_st,
                "local_st": local_st,
                "nat_win": block_value(stats_start, 0, 15),
                "nat_2": clean_pct(block_value(stats_start, 1, 16)),
                "nat_3": clean_pct(block_value(stats_start, 2, 17)),
                "local_win": block_value(stats_start, 3, 18),
                "local_2": clean_pct(block_value(stats_start, 4, 19)),
                "local_3": clean_pct(block_value(stats_start, 5, 20)),
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


def parse_biwako_player_ids(entry_html_path: Path) -> dict[int, int]:
    """Read BOATERS registration numbers without changing the shared text parser."""
    if not entry_html_path.is_file():
        return {}
    html = entry_html_path.read_text(encoding="utf-8", errors="replace")
    return {
        int(lane): int(reg_no)
        for lane, reg_no in re.findall(
            r'"boatNumber":([1-6]),"regN":([0-9]{4})',
            html,
        )
    }


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


def normalize_racer_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"[\s\u3000]+", "", text)


def load_prior_meeting_payloads(
    data_root: Path,
    slug: str,
    target_date: str,
    event_day: int,
) -> list[dict] | None:
    target = datetime.strptime(target_date, "%Y-%m-%d").date()
    prior_payloads = []
    for prior_day in range(1, event_day):
        prior_date = target - timedelta(days=event_day - prior_day)
        path = data_root / "venues" / slug / f"{prior_date:%Y%m%d}.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        races = payload.get("races")
        if (
            payload.get("date") != prior_date.isoformat()
            or payload.get("venueId") != slug
            or payload.get("eventDay") != prior_day
            or not isinstance(races, list)
            or len(races) != 12
            or any(len(race.get("racers") or []) != 6 for race in races)
        ):
            return None
        prior_payloads.append(payload)
    return prior_payloads


def annotate_no_prior_meeting_runs(payload: dict, slug: str, data_root: Path) -> dict:
    event_day = payload.get("eventDay")
    target_date = payload.get("date")
    if not isinstance(event_day, int) or event_day <= 1 or not isinstance(target_date, str):
        return payload

    prior_payloads = load_prior_meeting_payloads(
        data_root,
        slug,
        target_date,
        event_day,
    )
    prior_names = None
    if prior_payloads is not None:
        prior_names = {
            normalize_racer_name(racer.get("name"))
            for prior in prior_payloads
            for race in prior.get("races") or []
            for racer in race.get("racers") or []
            if normalize_racer_name(racer.get("name"))
        }
    evidence = {
        "source": "published_prior_meeting_data",
        "venue": slug,
        "checked_dates": [prior["date"] for prior in prior_payloads or []],
        "checked_event_days": [prior["eventDay"] for prior in prior_payloads or []],
        "prior_race_appearances": 0,
    }

    for race in payload.get("races") or []:
        for racer in race.get("racers") or []:
            for key in (
                "setsukan_status",
                "setsukan_evidence",
                "setsukan_first_entry_date",
            ):
                racer.pop(key, None)
            if racer.get("season_runs") or racer.get("season_groups"):
                continue
            name = normalize_racer_name(racer.get("name"))
            if prior_names is not None and name and name not in prior_names:
                racer["setsukan_status"] = "no_prior_meeting_runs"
                racer["setsukan_evidence"] = deepcopy(evidence)
                racer["setsukan_first_entry_date"] = target_date
    return payload


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


def race_data_gate(
    payload: dict,
    expected_date: str,
    expected_slug: str,
    expected_name: str,
) -> tuple[bool, str]:
    """Validate the publishable race-information domain independently."""
    if payload.get("date") != expected_date:
        return False, "race_date_mismatch"
    if payload.get("venueId") != expected_slug or payload.get("venue") != expected_name:
        return False, "race_venue_mismatch"
    races = payload.get("races")
    if not isinstance(races, list) or len(races) != 12:
        return False, "race_count_invalid"
    if sorted(int(race.get("race") or 0) for race in races) != list(range(1, 13)):
        return False, "race_numbers_invalid"
    for race in races:
        race_no = int(race.get("race") or 0)
        racers = race.get("racers")
        if not isinstance(racers, list) or len(racers) != 6:
            return False, f"race_{race_no:02d}_entry_count_invalid"
        lanes = sorted(int(racer.get("lane") or 0) for racer in racers)
        if lanes != list(range(1, 7)):
            return False, f"race_{race_no:02d}_lanes_invalid"
        if not re.fullmatch(r"[0-9]{1,2}:[0-9]{2}", str(race.get("deadline") or "")):
            return False, f"race_{race_no:02d}_deadline_invalid"
    return True, "ok"


def _probabilities_are_valid(values: object) -> bool:
    if not isinstance(values, dict) or set(map(str, values)) != {str(lane) for lane in range(1, 7)}:
        return False
    try:
        numbers = [float(values[str(lane)]) for lane in range(1, 7)]
    except (KeyError, TypeError, ValueError):
        return False
    return (
        all(math.isfinite(value) and 0.0 <= value <= 100.0 for value in numbers)
        and abs(sum(numbers) - 100.0) <= 0.5
    )


def _prediction_is_complete(prediction: object) -> bool:
    if not isinstance(prediction, dict):
        return False
    if any(not _probabilities_are_valid(prediction.get(key)) for key in ("win", "second", "third")):
        return False
    if not prediction.get("sab"):
        return False
    if not any(
        isinstance(prediction.get(key), list) and prediction[key]
        for key in ("ai", "aiUpset", "balance", "tickets")
    ):
        return False
    if prediction.get("fallback") or prediction.get("fallbackUsed"):
        return False
    return True


def prediction_payload_gate(payload: dict, expected_date: str) -> tuple[bool, str]:
    """Validate only the venue-engine prediction domain."""
    if payload.get("date") != expected_date:
        return False, "prediction_date_mismatch"
    engine = payload.get("engine")
    if not isinstance(engine, str) or not engine.strip():
        return False, "prediction_engine_missing"
    if engine == "deterministic_baseline_v1" or "baseline" in engine.lower():
        return False, "prediction_baseline_forbidden"
    if payload.get("fallback") or payload.get("fallbackUsed"):
        return False, "prediction_fallback_forbidden"
    predictions = payload.get("preds")
    if not isinstance(predictions, dict) or set(predictions) != {str(race) for race in range(1, 13)}:
        return False, "prediction_race_count_invalid"
    for race_no in range(1, 13):
        if not _prediction_is_complete(predictions.get(str(race_no))):
            return False, f"prediction_{race_no:02d}_invalid"
    return True, "ok"


def prediction_payload_is_complete(payload: dict, expected_date: str) -> bool:
    valid, _ = prediction_payload_gate(payload, expected_date)
    return valid


def load_same_day_payload(path: Path, expected_date: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return existing if existing.get("date") == expected_date else None


def preserve_prediction_payload(morning: dict, existing_path: Path) -> dict | None:
    existing = load_same_day_payload(existing_path, morning.get("date", ""))
    if existing is None:
        return morning
    engine = str(existing.get("engine") or "")
    if (
        engine == "deterministic_baseline_v1"
        or "baseline" in engine.lower()
        or existing.get("fallback")
        or existing.get("fallbackUsed")
    ):
        return None

    # The scheduled collector has no venue engines. It may enrich non-prediction
    # metadata, but it must never replace engine output, tickets, live data, or
    # confirmed results with empty or generic prediction data.
    return merge_validated_morning_metadata(existing, morning)


def prediction_envelope(
    payload: dict,
    race_no: int,
    prediction_available: bool,
    reason: str,
) -> dict:
    if not prediction_available:
        return {
            "status": "unavailable",
            "reason": reason,
            "engine": None,
            "engine_version": None,
            "probabilities": None,
            "sab": None,
            "tickets": None,
        }
    prediction = (payload.get("preds") or {}).get(str(race_no)) or {}
    return {
        "status": "ready",
        "reason": None,
        "engine": payload.get("engine"),
        "engine_version": payload.get("engineVersion") or payload.get("engine"),
        "probabilities": {
            "win": deepcopy(prediction.get("win")),
            "second": deepcopy(prediction.get("second")),
            "third": deepcopy(prediction.get("third")),
        },
        "sab": prediction.get("sab"),
        "tickets": {
            key: deepcopy(prediction.get(key))
            for key in ("ai", "aiUpset", "balance", "tickets")
            if isinstance(prediction.get(key), list)
        },
    }


def ashiya_race_prediction_is_complete(prediction: object) -> bool:
    """Validate and preserve the race-native Ashiya v1.6.1 prediction."""
    if not isinstance(prediction, dict):
        return False
    if prediction.get("status") != "ready":
        return False
    if prediction.get("engine") != "ashiya_prediction_engine":
        return False
    if str(prediction.get("engine_version") or "") != "1.6.1":
        return False
    if prediction.get("stage") not in {"pre", "live", "final"}:
        return False
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, dict) or any(
        not _probabilities_are_valid(probabilities.get(key))
        for key in ("win", "second", "third")
    ):
        return False
    sab = prediction.get("sab")
    if not isinstance(sab, dict) or not sab.get("grade"):
        return False
    tickets = prediction.get("tickets")
    if not isinstance(tickets, dict):
        return False
    return all(
        isinstance(tickets.get(key), list) and len(tickets[key]) == count
        for key, count in (
            ("main", 6),
            ("deviation", 2),
            ("upset", 2),
            ("all", 10),
        )
    )


def tokoname_race_prediction_is_complete(prediction: object) -> bool:
    """Validate the race-native Tokoname prediction without changing legacy venues."""
    if not isinstance(prediction, dict):
        return False
    if prediction.get("engine") != "tokoname_engine":
        return False
    if str(prediction.get("engine_version") or "") != "1.6":
        return False
    probabilities = prediction.get("probabilities")
    if not isinstance(probabilities, dict) or any(
        not _probabilities_are_valid(probabilities.get(key))
        for key in ("win", "second", "third")
    ):
        return False
    if not prediction.get("sab"):
        return False
    tickets = prediction.get("tickets")
    if not isinstance(tickets, dict):
        return False
    return all(
        isinstance(tickets.get(key), list) and len(tickets[key]) == count
        for key, count in (("main", 6), ("deviation", 2), ("upset", 2))
    )



def wakamatsu_race_prediction_is_complete(prediction: object) -> bool:
    """Validate and preserve the race-native Wakamatsu v2.1 prediction."""
    if not isinstance(prediction, dict):
        return False
    if prediction.get("engine") != "wakamatsu_engine_v2.1":
        return False
    if str(prediction.get("engineVersion") or "") != "2.1":
        return False
    if prediction.get("phase") not in {"pre", "final"}:
        return False
    if prediction.get("status") != "complete":
        return False
    if any(
        not _probabilities_are_valid(prediction.get(key))
        for key in ("win", "second", "third")
    ):
        return False
    if not prediction.get("sab"):
        return False

    tickets = prediction.get("tickets")
    if not isinstance(tickets, list) or len(tickets) != 10:
        return False

    combos = [
        ticket.get("combo")
        for ticket in tickets
        if isinstance(ticket, dict) and ticket.get("combo")
    ]
    return len(combos) == 10 and len(set(combos)) == 10


def biwako_race_prediction_is_complete(prediction: object) -> bool:
    """Validate and preserve the race-native Biwako v1.1 prediction."""
    if not isinstance(prediction, dict):
        return False
    if prediction.get("engine") != "biwako_engine_v1.1":
        return False
    if str(prediction.get("engineVersion") or "") != "1.1":
        return False
    if prediction.get("phase") not in {"preliminary", "final"}:
        return False
    if prediction.get("status") != "complete":
        return False
    if any(
        not _probabilities_are_valid(prediction.get(key))
        for key in ("win", "second", "third")
    ):
        return False
    if not prediction.get("sab"):
        return False
    tickets = prediction.get("tickets")
    if not isinstance(tickets, list) or len(tickets) != 10:
        return False
    combos = [
        ticket.get("combo")
        for ticket in tickets
        if isinstance(ticket, dict) and ticket.get("combo")
    ]
    return len(combos) == 10 and len(set(combos)) == 10

def attach_independent_race_domains(
    payload: dict,
    slug: str,
    prediction_available: bool,
    prediction_reason: str,
) -> dict:
    """Add explicit domains while retaining the legacy schema for compatibility."""
    predictions = payload.get("preds") or {}
    for race in payload.get("races") or []:
        race_no = int(race["race"])
        prediction = predictions.get(str(race_no)) or {}
        native_prediction = None
        if (
            slug == "ashiya"
            and ashiya_race_prediction_is_complete(race.get("prediction"))
        ):
            native_prediction = deepcopy(race.get("prediction"))
        elif (
            slug == "tokoname"
            and tokoname_race_prediction_is_complete(race.get("prediction"))
        ):
            native_prediction = deepcopy(race.get("prediction"))
        elif (
            slug == "wakamatsu"
            and wakamatsu_race_prediction_is_complete(race.get("prediction"))
        ):
            native_prediction = deepcopy(race.get("prediction"))
        elif (
            slug == "biwako"
            and biwako_race_prediction_is_complete(race.get("prediction"))
        ):
            native_prediction = deepcopy(race.get("prediction"))
        racers = deepcopy(race.get("racers") or [])
        race["race_meta"] = {
            "date": payload.get("date"),
            "venue": slug,
            "race_no": race_no,
            "deadline": race.get("deadline"),
            "day_no": race.get("eventDay", payload.get("eventDay")),
        }
        race["entries"] = racers
        race["setsukan"] = []
        for racer in sorted(racers, key=lambda item: int(item.get("lane") or 0)):
            setsukan = {
                "lane": racer.get("lane"),
                "season_runs": deepcopy(racer.get("season_runs") or []),
                "season_groups": deepcopy(racer.get("season_groups") or []),
            }
            for key in (
                "setsukan_status",
                "setsukan_evidence",
                "setsukan_first_entry_date",
            ):
                if key in racer:
                    setsukan[key] = deepcopy(racer[key])
            race["setsukan"].append(setsukan)
        if native_prediction is not None:
            race["prediction"] = native_prediction
        else:
            race["prediction"] = prediction_envelope(
                payload,
                race_no,
                prediction_available,
                prediction_reason,
            )
        race["live"] = deepcopy(race.get("live") or prediction.get("realtime") or {})
        race["odds"] = deepcopy(race.get("odds") or prediction.get("odds") or {})
        race["result"] = deepcopy(race.get("result") or prediction.get("result") or {})
    payload["predictionStatus"] = "ready" if prediction_available else "unavailable"
    payload["predictionReason"] = None if prediction_available else prediction_reason
    return payload


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
        if isinstance(previous.get("prediction_history"), dict):
            prediction["prediction_history"] = previous["prediction_history"]
        if previous.get("active_prediction_stage"):
            prediction["active_prediction_stage"] = previous["active_prediction_stage"]
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
        local_st_path = source_dir / "races" / f"race_{race_no:02d}_boaters_local_st.json"
        if local_st_path.is_file():
            try:
                local_rows = json.loads(local_st_path.read_text(encoding="utf-8")).get("racers") or []
            except (OSError, ValueError, json.JSONDecodeError):
                local_rows = []
            local_by_lane = {
                int(row.get("lane") or 0): row
                for row in local_rows
                if isinstance(row, dict) and int(row.get("lane") or 0) in range(1, 7)
            }
            for racer in racers:
                local_row = local_by_lane.get(int(racer.get("lane") or 0))
                if not local_row:
                    continue
                local_avg = str(local_row.get("boaters_local_avg_st") or "").strip()
                if re.fullmatch(r"0?\.\d{2}", local_avg):
                    racer["boaters_local_avg_st"] = local_avg
                local_rank = str(local_row.get("boaters_local_st_rank") or "").strip()
                if local_rank:
                    racer["boaters_local_st_rank"] = local_rank
        if venue["slug"] == "biwako":
            player_ids = parse_biwako_player_ids(entry_path.with_suffix(".html"))
            for racer in racers:
                lane = int(racer.get("lane") or 0)
                if lane in player_ids:
                    racer["player_id"] = player_ids[lane]
        deadline = parse_deadline(lines, race_no)
        race_type = parse_race_type(lines)
        entry_fixed = parse_entry_fixed(lines)
        if len(racers) != 6 or not deadline or not race_type:
            return None, {"reason": f"invalid_entry_{race_no:02d}", "racers": len(racers)}
        if race_no == 1:
            event_day, event_label = event_day_info(lines, date)
        races.append(
            {
                "race": race_no,
                "deadline": deadline,
                "title": venue["name"],
                "type": race_type,
                "entryFixed": entry_fixed,
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
            "venueId": venue["slug"],
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
    parser.add_argument(
        "--live-venue",
        choices=sorted(PREDICTION_VENUES),
        help=(
            "Publish an already-generated venue payload after a live prediction "
            "update without requiring morning fetch artifacts."
        ),
    )
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    source_root = Path(args.source_root)
    data_root = Path(args.data_root)
    date_dir = args.date.replace("-", "")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    configured = {venue["slug"]: venue for venue in config["venues"]}

    if args.live_venue:
        slug = args.live_venue
        dated_path = data_root / "venues" / slug / f"{date_dir}.json"
        if not dated_path.is_file():
            raise FileNotFoundError(dated_path)
        payload = json.loads(dated_path.read_text(encoding="utf-8"))
        if payload.get("date") != args.date or payload.get("venueId") != slug:
            raise RuntimeError(
                f"live venue identity mismatch: expected={slug}/{args.date} "
                f"actual={payload.get('venueId')}/{payload.get('date')}"
            )
        prediction_available, reason = prediction_payload_gate(
            payload,
            args.date,
        )
        if not prediction_available:
            raise RuntimeError(f"live prediction payload invalid: {reason}")

        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        write_text_atomic(data_root / "venues" / slug / "latest.json", serialized)

        now = datetime.now(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
        manifest_path = data_root / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("date") == args.date:
                for venue in manifest.get("venues") or []:
                    if venue.get("slug") != slug:
                        continue
                    venue.update(
                        {
                            "open": True,
                            "raceDataAvailable": True,
                            "race_data_available": True,
                            "predictionAvailable": True,
                            "prediction_available": True,
                            "predictionStatus": "ready",
                            "prediction_status": "ready",
                            "predictionReason": "",
                            "prediction_reason": "",
                            "dataPath": f"venues/{slug}/{date_dir}.json",
                            "latestPath": f"venues/{slug}/latest.json",
                        }
                    )
                    venue.pop("availabilityReason", None)
                    break
                manifest["updatedAt"] = now
                write_text_atomic(
                    manifest_path,
                    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                )

        report = {
            "date": args.date,
            "createdAt": now,
            "liveVenue": slug,
            "predictionAvailable": True,
            "predictionStatus": "ready",
            "engine": payload.get("engine"),
            "engineVersion": payload.get("engineVersion"),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

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
        precheck = fetch_status.get("precheck") or {}
        if str(precheck.get("reason", "")).startswith("precheck_failed:"):
            raise RuntimeError(f"{slug}: venue precheck failed: {precheck.get('reason')}")
        if precheck.get("open") is True and not (
            fetch_status.get("open") is True
            and fetch_status.get("entryCount") == 12
        ):
            raise RuntimeError(
                f"{slug}: scheduled meeting fetch incomplete: "
                f"returnCode={fetch_status.get('fetchReturnCode')} "
                f"entryCount={fetch_status.get('entryCount')}"
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
            if payload is not None:
                payload = annotate_no_prior_meeting_runs(payload, slug, data_root)
        race_data_available = payload is not None
        prediction_available = False
        prediction_reason = ""
        if race_data_available:
            race_data_available, race_reason = race_data_gate(
                payload,
                args.date,
                slug,
                venue["name"],
            )
            if not race_data_available:
                detail = {**detail, "reason": race_reason}
        if race_data_available:
            venue_dir = data_root / "venues" / slug
            venue_dir.mkdir(parents=True, exist_ok=True)
            existing_path = venue_dir / f"{date_dir}.json"
            if slug in PREDICTION_VENUES:
                preserved = preserve_prediction_payload(payload, existing_path)
                if preserved is not None:
                    payload = preserved
                prediction_available, prediction_gate_reason = prediction_payload_gate(
                    payload,
                    args.date,
                )
                prediction_reason = "" if prediction_available else "prediction_payload_unavailable"
                if not prediction_available:
                    detail = {
                        **detail,
                        "reason": prediction_reason,
                        "predictionGateReason": prediction_gate_reason,
                        "predictionRequired": True,
                    }
            else:
                payload["engine"] = None
                payload["preds"] = {}
                prediction_reason = "venue_engine_not_registered"
                detail = {**detail, "reason": prediction_reason}
            payload["venueId"] = slug
            payload = preserve_same_day_live_fields(payload, venue_dir / "latest.json")
            payload = attach_independent_race_domains(
                payload,
                slug,
                prediction_available,
                prediction_reason,
            )
            serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            write_text_atomic(existing_path, serialized)
            write_text_atomic(venue_dir / "latest.json", serialized)
        statuses[slug] = {
            "open": race_data_available,
            "raceDataAvailable": race_data_available,
            "predictionAvailable": prediction_available,
            "predictionStatus": "ready" if prediction_available else (
                "unavailable" if race_data_available else "not_running"
            ),
            "predictionReason": prediction_reason,
            "entryCount": 12 if race_data_available else 0,
            "firstDeadline": payload["races"][0]["deadline"] if race_data_available else "",
            "eventDay": payload.get("eventDay") if race_data_available else None,
            "eventDayLabel": payload.get("eventDayLabel") if race_data_available else None,
            "detail": detail,
        }

    manifest_venues = []
    for slug, name in ALL_VENUES:
        state = statuses.get(slug, {"open": False, "entryCount": 0, "firstDeadline": ""})
        item = {
            "slug": slug,
            "venue": slug,
            "name": name,
            "open": state["open"],
            "entryCount": state["entryCount"],
            "firstDeadline": state["firstDeadline"],
            "date": args.date,
            "dateDir": date_dir,
            "dataPath": f"venues/{slug}/{date_dir}.json" if state["open"] else "",
            "latestPath": f"venues/{slug}/latest.json" if state["open"] else "",
            "raceDataAvailable": state.get("raceDataAvailable", state["open"]),
            "race_data_available": state.get("raceDataAvailable", state["open"]),
            "predictionAvailable": state.get("predictionAvailable", False),
            "prediction_available": state.get("predictionAvailable", False),
        }
        reason = state.get("detail", {}).get("reason", "")
        if reason:
            item["availabilityReason"] = reason
        if slug in configured:
            prediction_status = state.get("predictionStatus", "not_running")
            prediction_reason = state.get("predictionReason", "")
            item["predictionStatus"] = prediction_status
            item["prediction_status"] = prediction_status
            item["predictionReason"] = prediction_reason
            item["prediction_reason"] = prediction_reason
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
