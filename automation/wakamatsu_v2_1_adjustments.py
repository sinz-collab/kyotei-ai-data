from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import itertools
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = REPO_ROOT / "engines" / "wakamatsu_v2" / "engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import wakamatsu_engine as core

LANES = (1, 2, 3, 4, 5, 6)
ENGINE_ID = "wakamatsu_engine_v2.2"
ENGINE_VERSION = "2.2"
COURSE_WEIGHTS = (0.35, 0.35, 0.20, 0.10)  # ability, player-course, course, local
CLASS_WEIGHT = {"A1": 1.18, "A2": 1.08, "B1": 1.00, "B2": 0.60}

_ORIGINAL_PREDICT = core.WakamatsuEngine.predict
_ORIGINAL_BASE_PROBS = core.WakamatsuEngine._base_probs
_ORIGINAL_TICKETS = core.WakamatsuEngine._tickets
_ORIGINAL_SCENARIO_MULTIPLIER = core.WakamatsuEngine._scenario_multiplier
_PATCHED = False


def _number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", "-", "―"):
        return default
    try:
        out = float(re.sub(r"[^0-9.\-]", "", str(value)))
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _normalize(values: list[float]) -> list[float]:
    clipped = [max(1e-9, float(v)) for v in values]
    total = sum(clipped)
    return [v / total for v in clipped]


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _source_payload(date_text: str | None) -> dict:
    if not date_text:
        return {}
    compact = str(date_text).replace("-", "")
    return _load_json(REPO_ROOT / "data" / "venues" / "wakamatsu" / f"{compact}.json")


def _source_race(payload: dict, race_no: int) -> dict:
    for race in payload.get("races") or []:
        try:
            if int(race.get("race") or 0) == race_no:
                return race
        except Exception:
            continue
    return {}


def _live_data(date_text: str | None, race_no: int, filename: str) -> dict:
    if not date_text:
        return {}
    vps_root = Path("/opt/sinz-edge/data/live")
    repo_root = Path(__file__).resolve().parents[1] / "data" / "live"
    live_root = vps_root if vps_root.exists() else repo_root
    path = live_root / str(date_text) / "wakamatsu" / f"{race_no:02d}" / filename
    wrapper = _load_json(path)
    data = wrapper.get("data") if isinstance(wrapper, dict) else None
    return data if isinstance(data, dict) else {}


def _parse_hhmm(text: Any) -> int | None:
    m = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)", str(text or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if 0 <= h <= 23 and 0 <= mi <= 59:
        return h * 60 + mi
    return None


def _deadline_minutes(race: dict) -> int | None:
    return _parse_hhmm(race.get("start_time"))


def _tide_events(payload: dict) -> list[dict[str, Any]]:
    tide = payload.get("tide") or {}
    events: list[dict[str, Any]] = []
    raw_events = tide.get("events") or tide.get("tideEvents") or []
    if isinstance(raw_events, list):
        for row in raw_events:
            if not isinstance(row, dict):
                continue
            minute = _parse_hhmm(row.get("time") or row.get("datetime") or row.get("label"))
            label = str(row.get("type") or row.get("event") or row.get("name") or row.get("label") or "")
            if minute is None:
                continue
            kind = "low" if ("干潮" in label or "low" in label.lower()) else "high" if ("満潮" in label or "high" in label.lower()) else None
            if kind:
                events.append({"minute": minute, "kind": kind, "label": label})
    if events:
        return sorted(events, key=lambda x: x["minute"])

    texts = []
    for value in tide.values() if isinstance(tide, dict) else []:
        if isinstance(value, str):
            texts.append(value)
    text = " / ".join(texts)
    for m in re.finditer(r"(\d{1,2}:\d{2})\s*(満潮|干潮)", text):
        minute = _parse_hhmm(m.group(1))
        if minute is not None:
            events.append({"minute": minute, "kind": "high" if m.group(2) == "満潮" else "low", "label": m.group(2)})
    return sorted(events, key=lambda x: x["minute"])


def _derive_tide(payload: dict, race: dict) -> dict[str, Any]:
    minute = _deadline_minutes(race)
    events = _tide_events(payload)
    if minute is None or not events:
        return {"phase": race.get("tide_phase"), "minutes_to_low": race.get("minutes_to_low_tide"), "zone": race.get("tide_zone")}

    previous = None
    next_event = None
    for event in events:
        if event["minute"] <= minute:
            previous = event
        elif next_event is None:
            next_event = event
    if previous is None:
        previous = events[-1].copy()
        previous["minute"] -= 24 * 60
    if next_event is None:
        next_event = events[0].copy()
        next_event["minute"] += 24 * 60

    phase = None
    minutes_to_low = None
    if previous["kind"] == "low" and next_event["kind"] == "high":
        phase = "rising"
        minutes_to_low = -(minute - previous["minute"])
    elif previous["kind"] == "high" and next_event["kind"] == "low":
        phase = "falling"
        minutes_to_low = next_event["minute"] - minute
    elif previous["kind"] == "low":
        phase = "rising"
        minutes_to_low = -(minute - previous["minute"])
    elif next_event["kind"] == "low":
        phase = "falling"
        minutes_to_low = next_event["minute"] - minute

    nearest_distance = min(abs(minute - previous["minute"]), abs(next_event["minute"] - minute))
    if nearest_distance <= 20:
        zone = "slack"
    elif abs(minutes_to_low or 9999) <= 90:
        zone = "low"
    else:
        zone = "mid"
    return {"phase": phase, "minutes_to_low": minutes_to_low, "zone": zone}


def _resolve_actual_entry(race: dict, direct: dict, exhibition: dict) -> tuple[dict[int, int], str]:
    actual = direct.get("actual_entry") or direct.get("actualEntry")
    if isinstance(actual, list) and len(actual) == 6:
        lanes = [int(x) for x in actual if int(x) in LANES]
        if sorted(lanes) == list(LANES):
            return {lane: course for course, lane in enumerate(lanes, start=1)}, "direct.actual_entry"

    entries = exhibition.get("entries") or []
    mapping: dict[int, int] = {}
    for row in entries:
        if not isinstance(row, dict):
            continue
        lane = int(row.get("lane") or 0)
        course = int(row.get("exhibition_course") or row.get("course") or 0)
        if lane in LANES and course in LANES:
            mapping[lane] = course
    if len(mapping) == 6 and sorted(mapping.values()) == list(LANES):
        return mapping, "exhibition.exhibition_course"

    mapping = {}
    for boat in race.get("boats") or []:
        lane = int(boat.get("lane") or 0)
        course = int(boat.get("entry_course") or lane)
        if lane in LANES and course in LANES:
            mapping[lane] = course
    return mapping, "race_input.entry_course"


def _ability_raw(boat: dict, position: str, local: bool = False) -> float:
    prefix = "local" if local else "nat"
    win = max(0.0, float(boat.get(f"{prefix}_win") or 0.0))
    p2 = max(0.0, float(boat.get(f"{prefix}_2") or 0.0))
    p3 = max(0.0, float(boat.get(f"{prefix}_3") or 0.0))
    cls = CLASS_WEIGHT.get(str(boat.get("class") or "B1"), 1.0)
    if local and win <= 0.01:
        return 0.0
    if position == "win":
        value = 0.62 * max(0.02, win / 7.5) ** 1.6 + 0.25 * max(0.01, p2 / 60.0) ** 1.3 + 0.13 * max(0.01, p3 / 80.0)
    elif position == "second":
        value = 0.20 * max(0.02, win / 7.5) + 0.50 * max(0.01, p2 / 60.0) ** 1.25 + 0.30 * max(0.01, p3 / 80.0)
    else:
        value = 0.10 * max(0.02, win / 7.5) + 0.25 * max(0.01, p2 / 60.0) + 0.65 * max(0.01, p3 / 80.0) ** 1.2
    return max(1e-5, cls * value)


def _pct(value: Any) -> float | None:
    v = _number(value, None)
    if v is None:
        return None
    return v / 100.0 if v > 1.0 else v


def _player_course_rate(engine: core.WakamatsuEngine, boat: dict, position: str) -> tuple[float, dict[str, Any]]:
    lane = int(boat["lane"])
    course = int(boat.get("entry_course") or lane)
    player_id = boat.get("player_id")
    prior_idx = {"win": 0, "second": 1, "third": 2}[position]
    prior = float(core.COURSE_PRIORS.get(course, core.COURSE_PRIORS[lane])[prior_idx])
    stats = engine._lookup_player_course(player_id, lane, course)
    if not stats:
        return prior, {"available": False, "fallback": "course_prior"}
    starts = max(0, int(stats.get("starts") or 0))
    rel = str(stats.get("reliability") or "")
    shrink = core.RELIABILITY_WEIGHT.get(rel, 0.20) * min(1.0, starts / 30.0)
    key = {"win": "win_rate", "second": "second_rate", "third": "third_rate"}[position]
    observed = _pct(stats.get(key))
    rate = prior if observed is None else prior * (1.0 - shrink) + observed * shrink
    return max(1e-5, rate), {"available": True, "starts": starts, "reliability": rel, "shrink": round(shrink, 4), "observed": observed}


def _precompute_bases(engine: core.WakamatsuEngine, race: dict) -> dict[int, dict[str, Any]]:
    boats = sorted(race.get("boats") or [], key=lambda x: int(x["lane"]))
    audits: dict[int, dict[str, Any]] = {int(b["lane"]): {} for b in boats}
    for position in ("win", "second", "third"):
        ability_raw = [_ability_raw(b, position, False) for b in boats]
        ability_share = _normalize(ability_raw)

        pc_rates = []
        pc_audit = []
        for b in boats:
            rate, audit = _player_course_rate(engine, b, position)
            pc_rates.append(rate)
            pc_audit.append(audit)
        pc_share = _normalize(pc_rates)

        priors = [float(core.COURSE_PRIORS[int(b.get("entry_course") or b["lane"])][{"win": 0, "second": 1, "third": 2}[position]]) for b in boats]
        course_share = _normalize(priors)

        local_raw = [_ability_raw(b, position, True) for b in boats]
        if sum(local_raw) <= 1e-8:
            local_share = ability_share[:]
        else:
            # Missing local samples fall back to half of national ability rather than zero.
            local_raw = [v if v > 0 else ability_raw[i] * 0.5 for i, v in enumerate(local_raw)]
            local_share = _normalize(local_raw)

        combined = [
            COURSE_WEIGHTS[0] * ability_share[i]
            + COURSE_WEIGHTS[1] * pc_share[i]
            + COURSE_WEIGHTS[2] * course_share[i]
            + COURSE_WEIGHTS[3] * local_share[i]
            for i in range(6)
        ]
        combined = _normalize(combined)
        for i, boat in enumerate(boats):
            lane = int(boat["lane"])
            boat.setdefault("_v22_base", {})[position] = combined[i]
            audits[lane][position] = {
                "ability_share": round(ability_share[i], 6),
                "player_course_share": round(pc_share[i], 6),
                "course_share": round(course_share[i], 6),
                "local_share": round(local_share[i], 6),
                "combined": round(combined[i], 6),
                "player_course": pc_audit[i],
            }
    return audits


def _bounded(value: float, delta: float, cap: float) -> float:
    return max(1e-6, value + max(-cap, min(cap, delta)))


def _enhanced_meeting(engine: core.WakamatsuEngine, boat: dict, bp: Any) -> dict[str, Any]:
    form = engine._derive_meeting_form(boat)
    if not form.get("available"):
        return {"available": False}
    event_day = int(boat.get("event_day") or 1)
    n = int(form.get("run_count") or 0)
    if event_day <= 1:
        meeting_weight = 0.0
    elif event_day == 2:
        meeting_weight = 0.90
    elif event_day == 3:
        meeting_weight = 1.00
    elif event_day <= 5:
        meeting_weight = 1.08
    else:
        meeting_weight = 1.12
    trend_conf = 0.0 if n <= 1 else 0.50 if n == 2 else 0.75 if n == 3 else 1.0
    trend_weight = 0.65 * trend_conf
    level = float(form.get("level_score") or 0.0)
    recent = float(form.get("recent_score") or 0.0)
    stability = float(form.get("stability_score") or 0.0)
    trend = float(form.get("trend_score") or 0.0)
    combined_level = max(-1.0, min(1.0, 0.68 * level + 0.25 * recent + 0.07 * stability))
    combined_trend = max(-1.0, min(1.0, trend))
    # v2.2: meeting achievement component.
    # Reward concrete finishes separately from level/trend.
    achievement_win = 0.0
    achievement_second = 0.0
    achievement_third = 0.0
    poor_finishes = 0

    runs = boat.get("meeting_runs") or boat.get("season_runs") or []

    for run in runs:
        finish_text = str(run.get("finish") or "")
        course = int(run.get("entry_course") or run.get("course") or 0)

        if finish_text.startswith("1"):
            bonus = 0.015 if course >= 4 else (0.013 if course == 3 else 0.010)
            achievement_win += bonus
            achievement_second += bonus * 0.45
            achievement_third += bonus * 0.25
        elif finish_text.startswith("2"):
            bonus = 0.010 if course >= 4 else (0.008 if course == 3 else 0.006)
            achievement_win += bonus * 0.35
            achievement_second += bonus
            achievement_third += bonus * 0.50
        elif finish_text.startswith("3"):
            bonus = 0.005 if course >= 4 else (0.004 if course == 3 else 0.003)
            achievement_win += bonus * 0.20
            achievement_second += bonus * 0.55
            achievement_third += bonus
        elif finish_text.startswith(("5", "6")):
            poor_finishes += 1

    if poor_finishes >= 2:
        achievement_win -= 0.010
        achievement_second -= 0.007
        achievement_third -= 0.004
    elif poor_finishes == 1:
        achievement_win -= 0.004
        achievement_second -= 0.002
        achievement_third -= 0.001

    achievement_win = max(-0.015, min(0.030, achievement_win))
    achievement_second = max(-0.012, min(0.025, achievement_second))
    achievement_third = max(-0.008, min(0.020, achievement_third))

    win_delta = (
        0.026 * combined_level * meeting_weight
        + 0.014 * combined_trend * trend_weight
        + achievement_win
    )
    second_delta = (
        0.024 * combined_level * meeting_weight
        + 0.012 * combined_trend * trend_weight
        + achievement_second
    )
    third_delta = (
        0.020 * combined_level * meeting_weight
        + 0.008 * combined_trend * trend_weight
        + achievement_third
    )
    bp.win = _bounded(bp.win, win_delta, 0.055)
    bp.second = _bounded(bp.second, second_delta, 0.050)
    bp.third = _bounded(bp.third, third_delta, 0.040)
    bp.top3 = bp.win + bp.second + bp.third
    bp.notes.append(f"meeting_v22:day{event_day}:n{n}:trend_conf{trend_conf:.2f}")
    return {
        **form,
        "meeting_weight_v22": meeting_weight,
        "trend_confidence_v22": trend_conf,
        "win_delta_v22": round(win_delta, 6),
        "second_delta_v22": round(second_delta, 6),
        "third_delta_v22": round(third_delta, 6),
    }


def _patched_base_probs(self: core.WakamatsuEngine, boat: dict[str, Any], time_band: str, tide_type: str | None):
    base = boat.get("_v22_base") or {}
    if not all(k in base for k in ("win", "second", "third")):
        return _ORIGINAL_BASE_PROBS(self, boat, time_band, tide_type)

    original_player_id = boat.get("player_id")
    working = dict(boat)
    # Player-course/local/meeting are already represented in the new base or v2.2 meeting pass.
    working["player_id"] = None
    working["local_win"] = 0.0
    working["local_2"] = 0.0
    working["local_3"] = 0.0
    working["meeting_runs"] = []
    working["season_runs"] = []

    course = int(working.get("entry_course") or working["lane"])
    previous = core.COURSE_PRIORS.get(course)
    core.COURSE_PRIORS[course] = (float(base["win"]), float(base["second"]), float(base["third"]))
    try:
        bp = _ORIGINAL_BASE_PROBS(self, working, time_band, tide_type)
    finally:
        if previous is not None:
            core.COURSE_PRIORS[course] = previous
    bp.player_id = original_player_id
    audit = _enhanced_meeting(self, boat, bp)
    boat["_v22_meeting_audit"] = audit
    cache = getattr(self, "_v22_meeting_audit_by_lane", None)
    if cache is None:
        cache = {}
        self._v22_meeting_audit_by_lane = cache
    lane_key = str(int(boat.get("lane") or bp.lane))
    cache[lane_key] = audit
    return bp


def _patched_scenario_multiplier(
    self: core.WakamatsuEngine,
    combo: tuple[int, int, int],
    scenarios: dict[str, float],
    entry_course_by_lane: dict[int, int],
    preferred_course4_outer_lane: int | None = None,
    attack_lanes: set[int] | None = None,
):
    # Run the published multiplier without the v1.9 off-by-one attack-lane block,
    # then apply that block against the correct 1-based head lane.
    mult, tags = _ORIGINAL_SCENARIO_MULTIPLIER(
        self, combo, scenarios, entry_course_by_lane,
        preferred_course4_outer_lane, None,
    )
    a, b, c = [x + 1 for x in combo]
    if attack_lanes and a in attack_lanes:
        head_course = entry_course_by_lane.get(a, a)
        outside_lane = next(
            (lane for lane, course in entry_course_by_lane.items() if course == head_course + 1),
            None,
        )
        if outside_lane is not None and b == outside_lane and c == 1:
            mult *= 1.30
            tags.append("attack_head_outside_second_inside_third")
        elif outside_lane is not None and b == 1 and c == outside_lane:
            mult *= 0.98
            tags.append("attack_head_inside_second_outside_third")
    return mult, tags


def _patched_tickets(
    self: core.WakamatsuEngine,
    win: np.ndarray,
    second: np.ndarray,
    third: np.ndarray,
    scenarios: dict[str, float],
    entry_course_by_lane: dict[int, int],
    preferred_course4_outer_lane: int | None = None,
    attack_lanes: set[int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    combos = []
    for combo in itertools.permutations(range(6), 3):
        raw = self._combo_probability(*combo, win, second, third)
        mult, tags = self._scenario_multiplier(
            combo,
            scenarios,
            entry_course_by_lane,
            preferred_course4_outer_lane,
            attack_lanes,
        )
        combos.append({
            "ticket": f"{combo[0] + 1}-{combo[1] + 1}-{combo[2] + 1}",
            "probability": raw * mult,
            "raw_probability": raw,
            "scenario_multiplier": mult,
            "head": combo[0] + 1,
            "tags": tags,
        })
    total = sum(x["probability"] for x in combos)
    for x in combos:
        x["probability"] /= max(total, 1e-12)
    combos.sort(key=lambda x: x["probability"], reverse=True)
    self._v22_ticket_rank_audit = [
        {
            "rank": idx + 1,
            "ticket": row["ticket"],
            "probability": round(row["probability"], 8),
            "raw_probability": round(row["raw_probability"], 8),
            "scenario_multiplier": round(row["scenario_multiplier"], 6),
            "scenario_tags": list(row["tags"]),
        }
        for idx, row in enumerate(combos[:20])
    ]
    top10 = combos[:10]

    def clean(rows):
        return [
            {
                "ticket": r["ticket"],
                "probability": round(r["probability"], 6),
                "scenario_tags": r["tags"],
            }
            for r in rows
        ]

    return {
        "main": clean(top10[:6]),
        "deviation": clean(top10[6:8]),
        "upset": clean(top10[8:10]),
    }


def _input_audit(race: dict, source: dict, direct: dict, exhibition: dict, entry_source: str, base_audit: dict) -> dict[str, Any]:
    boats = race.get("boats") or []
    entry_courses = {int(b["lane"]): int(b.get("entry_course") or b["lane"]) for b in boats}
    checks = {
        "playerCourseDb": all(any((base_audit.get(int(b["lane"])) or {}).get(pos, {}).get("player_course", {}).get("available") for pos in ("win", "second", "third")) for b in boats),
        "localStats": all(float(b.get("local_win") or 0.0) > 0.01 for b in boats),
        "meetingForm": all(bool(b.get("meeting_runs")) for b in boats),
        "motor": all(b.get("motor_2") is not None and b.get("motor_3") is not None for b in boats),
        "tideType": bool(race.get("tide_type")),
        "tidePhase": bool(race.get("tide_phase")),
        "minutesToLow": race.get("minutes_to_low_tide") is not None,
        "wind": race.get("wind_speed") is not None,
        "wave": race.get("wave_height") is not None,
        "stabilizer": direct.get("stabilizer") is not None,
        "exhibition": len(exhibition.get("entries") or []) == 6,
        "actualEntry": sorted(entry_courses.values()) == list(LANES),
        "escapeRate": any(b.get("boaters_escape_rate") is not None for b in boats),
        "normalizationTarget": True,
        "oddsUsed": False,
    }
    critical = ("playerCourseDb", "motor", "tideType", "tidePhase", "wind", "wave", "actualEntry", "escapeRate")
    missing = [key for key in critical if not checks.get(key)]
    return {
        "version": ENGINE_VERSION,
        "status": "complete" if not missing else "partial",
        "missingCritical": missing,
        "checks": checks,
        "actualEntrySource": entry_source,
        "actualEntry": [lane for lane, _ in sorted(entry_courses.items(), key=lambda item: item[1])],
        "windSpeed": race.get("wind_speed"),
        "waveHeight": race.get("wave_height"),
        "stabilizer": direct.get("stabilizer"),
        "tideType": race.get("tide_type"),
        "tidePhase": race.get("tide_phase"),
        "tideZone": race.get("tide_zone"),
        "minutesToLowTide": race.get("minutes_to_low_tide"),
        "baseFormula": {"ability": 0.35, "playerCourse": 0.35, "wakamatsuCourse": 0.20, "local": 0.10},
        "meetingFormula": "level/recent priority + trend*sample_confidence; n2=0.50,n3=0.75,n4+=1.00",
        "ticketFormula": "top10 probability after scenario multiplier; roles 6/2/2",
    }


def _patched_predict(self: core.WakamatsuEngine, race: dict[str, Any]) -> dict[str, Any]:
    self._v22_meeting_audit_by_lane = {}
    self._v22_ticket_rank_audit = []
    enriched = deepcopy(race)
    date_text = str(enriched.get("date") or "")
    race_no = int(enriched.get("race_no") or 0)
    payload = _source_payload(date_text)
    source = _source_race(payload, race_no)
    direct = _live_data(date_text, race_no, "direct.json")
    exhibition = _live_data(date_text, race_no, "exhibition.json")

    source_by_lane = {int(x.get("lane") or 0): x for x in source.get("racers") or [] if int(x.get("lane") or 0) in LANES}
    boat_by_lane = {int(x.get("lane") or 0): x for x in enriched.get("boats") or [] if int(x.get("lane") or 0) in LANES}

    entry_map, entry_source = _resolve_actual_entry(enriched, direct, exhibition)
    for lane, boat in boat_by_lane.items():
        if lane in entry_map:
            boat["entry_course"] = entry_map[lane]
        src = source_by_lane.get(lane, {})
        for key in (
            "boaters_escape_rate",
            "boaters_sashare_rate",
            "boaters_makurare_rate",
            "boaters_makurare_zashi_rate",
            "boaters_nigashi_rate",
            "boaters_sashi_rate",
            "boaters_makuri_rate",
            "boaters_makuri_sashi_rate",
            "boaters_kimarite_starts",
        ):
            if src.get(key) not in (None, ""):
                boat[key] = src.get(key)

    if direct:
        if direct.get("wind_speed") is not None:
            enriched["wind_speed"] = _number(direct.get("wind_speed"), 0.0)
        if direct.get("wave_height") is not None:
            enriched["wave_height"] = _number(direct.get("wave_height"), 0.0)
        if direct.get("wind_direction") is not None:
            enriched["wind_dir"] = direct.get("wind_direction")
        enriched["stabilizer"] = direct.get("stabilizer")

    tide = _derive_tide(payload, enriched)
    if not enriched.get("tide_phase") and tide.get("phase"):
        enriched["tide_phase"] = tide["phase"]
    if enriched.get("minutes_to_low_tide") is None and tide.get("minutes_to_low") is not None:
        enriched["minutes_to_low_tide"] = tide["minutes_to_low"]
    if not enriched.get("tide_zone") and tide.get("zone"):
        enriched["tide_zone"] = tide["zone"]

    # Propagate race-level tide state to boats before base calculation.
    for boat in enriched.get("boats") or []:
        boat["event_day"] = enriched.get("event_day", 1)
        boat["minutes_to_low_tide"] = enriched.get("minutes_to_low_tide")
        boat["tide_phase"] = enriched.get("tide_phase")

    base_audit = _precompute_bases(self, enriched)
    result = _ORIGINAL_PREDICT(self, enriched)

    audit = _input_audit(enriched, source, direct, exhibition, entry_source, base_audit)
    meeting_audit = dict(getattr(self, "_v22_meeting_audit_by_lane", {}) or {})
    result["full_reflection"] = {
        "status": audit["status"],
        "engine": ENGINE_ID,
        "version": ENGINE_VERSION,
        "inputAudit": audit,
        "baseAudit": base_audit,
        "meetingAudit": meeting_audit,
    }
    result["v22_input_audit"] = audit
    result["v22_base_audit"] = base_audit
    result["v22_meeting_audit"] = meeting_audit
    result["v22_ticket_rank_audit"] = list(getattr(self, "_v22_ticket_rank_audit", []) or [])
    result.setdefault("race_context", {}).update({
        "tide_phase": enriched.get("tide_phase"),
        "tide_zone": enriched.get("tide_zone"),
        "minutes_to_low_tide": enriched.get("minutes_to_low_tide"),
        "wind_dir": enriched.get("wind_dir"),
        "wind_speed": enriched.get("wind_speed"),
        "wave_height": enriched.get("wave_height"),
        "stabilizer": enriched.get("stabilizer"),
        "entry_changed": any(int(b.get("entry_course") or b["lane"]) != int(b["lane"]) for b in enriched.get("boats") or []),
    })
    return result


def _install_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    core.WakamatsuEngine._base_probs = _patched_base_probs
    core.WakamatsuEngine._tickets = _patched_tickets
    core.WakamatsuEngine._scenario_multiplier = _patched_scenario_multiplier
    core.WakamatsuEngine.predict = _patched_predict
    _PATCHED = True


_install_patch()


def apply_v21_adjustments(result: dict, race_input: dict) -> dict:
    """Compatibility entry point used by apply_wakamatsu_v2.py.

    v2.2 is applied inside WakamatsuEngine.predict so probabilities, scenarios,
    SAB and tickets stay internally consistent. This function must not apply a
    second probability correction.
    """
    adjusted = deepcopy(result)
    adjusted["v22_adjustments"] = {
        "engine": ENGINE_ID,
        "version": ENGINE_VERSION,
        "applied_inside_predict": True,
        "odds_used": False,
        "normalized": True,
        "ticket_selection": "probability_top10_6_2_2",
    }
    return adjusted
