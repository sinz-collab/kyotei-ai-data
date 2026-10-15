"""Staging-only Wakamatsu v3 integration.

The runner consumes the JSON produced by the existing Wakamatsu LIVE engine,
then writes a separate staging JSON.  It never edits the source/public JSON.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime
import json
import math
from pathlib import Path
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

try:
    from .wakamatsu_fly_tickets_v1 import fly_level, normalize_event_type
    from .wakamatsu_v3_live import fetch_validated_tide, predict_v3
except ImportError:  # pragma: no cover - direct script execution
    from wakamatsu_fly_tickets_v1 import fly_level, normalize_event_type
    from wakamatsu_v3_live import fetch_validated_tide, predict_v3


REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = "wakamatsu_v3_lane1_tide_frozen"
MORNING_PROVISIONAL_MODEL_NAME = "morning_lane1_win_complement_provisional"
LIVE_REQUIRED = ("direct", "exhibition", "original_exhibition")
JST = ZoneInfo("Asia/Tokyo")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"json_object_required: {path}")
    return value


def _number(value: Any) -> float | None:
    if value in (None, "", "-", "―") or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else None


def morning_provisional_probability(prediction: Any) -> float | None:
    """Return 100 - lane-1 morning win probability without using LIVE fields."""
    if not isinstance(prediction, dict) or prediction.get("phase") != "pre":
        return None
    lane1_win = _number((prediction.get("win") or {}).get("1"))
    if lane1_win is None or not math.isfinite(lane1_win) or not 0 <= lane1_win <= 100:
        return None
    return 100.0 - lane1_win


def live_inputs_complete(race: dict[str, Any]) -> bool:
    if not all(isinstance(race.get(key), dict) and race.get(key) for key in LIVE_REQUIRED):
        return False
    final = race.get("predictionFinal")
    if not isinstance(final, dict) or final.get("phase") != "final":
        return False
    entries = (race.get("original_exhibition") or {}).get("entries") or []
    by_lane = {int(item.get("lane") or 0): item for item in entries if isinstance(item, dict)}
    if set(by_lane) != set(range(1, 7)):
        return False
    for lane in range(1, 7):
        if any(_number(by_lane[lane].get(key)) is None for key in ("lap_time", "turn_time", "straight_time")):
            return False
        if _number((final.get("win") or {}).get(str(lane))) is None:
            return False
    return True


def live_model_row(race: dict[str, Any]) -> dict[str, Any]:
    """Map only pre-deadline LIVE fields; result and odds are intentionally unread."""
    if not live_inputs_complete(race):
        raise ValueError("live_exhibition_inputs_incomplete")
    final = race["predictionFinal"]
    racers = {
        int(item.get("lane") or 0): item
        for item in (race.get("racers") or [])
        if isinstance(item, dict)
    }
    original = {
        int(item.get("lane") or 0): item
        for item in race["original_exhibition"]["entries"]
        if isinstance(item, dict)
    }
    lane1 = racers.get(1, {})
    context = final.get("raceContext") or {}
    course = (
        (((final.get("diagnostics") or {}).get("fullReflection") or {}).get("baseAudit") or {})
        .get("1", {})
        .get("win", {})
        .get("player_course", {})
    )
    observed = _number(course.get("observed"))
    row: dict[str, Any] = {
        "race_no": race.get("race"),
        "boat1_player_course_win_rate_pct": None if observed is None else observed * 100,
        "boat1_player_course_starts": course.get("starts"),
        "boat1_escape_rate_pct": lane1.get("boaters_escape_rate"),
        "boat1_local_win_rate": lane1.get("local_win"),
        "pred_wind_speed_mps": context.get("wind_speed"),
        "tide_phase": context.get("tide_phase"),
    }
    for lane in range(1, 7):
        racer = racers.get(lane, {})
        original_entry = original[lane]
        row[f"boat{lane}_live_win_pct"] = (final.get("win") or {}).get(str(lane))
        row[f"boat{lane}_avg_st"] = racer.get("avg_st")
        row[f"boat{lane}_original_lap_time"] = original_entry.get("lap_time")
        row[f"boat{lane}_original_turn_time"] = original_entry.get("turn_time")
        row[f"boat{lane}_original_straight_time"] = original_entry.get("straight_time")
    return row


def _set_probability(
    prediction: dict[str, Any],
    probability: float | None,
    *,
    source: str | None,
    status: str,
    reason: str,
    event_type: str,
    model: str,
    display_stage: str,
    provisional: bool,
) -> None:
    prediction["lane1FlyProbability"] = None if probability is None else round(probability, 2)
    prediction["lane1FlyProbabilitySource"] = source
    prediction["lane1FlyLevel"] = fly_level(probability, event_type)
    prediction["lane1FlyProbabilityModel"] = model
    prediction["lane1FlyProbabilityStatus"] = status
    prediction["lane1FlyProbabilityReason"] = reason
    prediction["lane1FlyProbabilityDisplayStage"] = display_stage
    prediction["lane1FlyProbabilityIsProvisional"] = provisional


def build_staging_payload(
    source: dict[str, Any],
    target_date: str,
    tide_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if source.get("venueId") not in (None, "wakamatsu"):
        raise RuntimeError("wakamatsu_payload_required")
    if source.get("date") != target_date:
        raise RuntimeError("source_date_mismatch")
    staged = deepcopy(source)
    event_type = normalize_event_type(staged.get("eventType"), staged.get("eventLabel"))
    logs: list[dict[str, Any]] = []
    available = 0
    unavailable = 0
    live_not_ready = 0
    morning_available = 0
    morning_unavailable = 0
    calculated_at = datetime.now(JST).isoformat()

    for race in staged.get("races") or []:
        race_no = int(race.get("race") or 0)
        pre = race.get("predictionPre")
        morning_probability = morning_provisional_probability(pre)
        if isinstance(pre, dict):
            morning_ok = morning_probability is not None
            _set_probability(
                pre,
                morning_probability,
                source="morning_provisional" if morning_ok else None,
                status="ok" if morning_ok else "unavailable",
                reason="validated_morning_complement" if morning_ok else "morning_lane1_win_unavailable",
                event_type=event_type,
                model=MORNING_PROVISIONAL_MODEL_NAME,
                display_stage="morning",
                provisional=True,
            )
            if morning_ok:
                morning_available += 1
            else:
                morning_unavailable += 1

        final = race.get("predictionFinal")
        if not isinstance(final, dict) or not live_inputs_complete(race):
            live_not_ready += 1
            result = {
                "status": "unavailable",
                "reason": "live_exhibition_inputs_incomplete",
                "lane1FlyProbability": None,
            }
        else:
            result = predict_v3(
                live_model_row(race),
                tide_payload,
                target_date=target_date,
                deadline=str(race.get("deadline") or ""),
                input_stage="live",
            )

        if isinstance(final, dict):
            probability = result.get("lane1FlyProbability")
            fly_prediction = final.get("flyPrediction")
            if (
                isinstance(fly_prediction, dict)
                and fly_prediction.get("source") == "live_pending_v3"
            ):
                fly_prediction["source"] = "live"
            _set_probability(
                final,
                probability,
                source="live",
                status=str(result.get("status") or "unavailable"),
                reason=str(result.get("reason") or "unknown"),
                event_type=event_type,
                model=MODEL_NAME,
                display_stage="live",
                provisional=False,
            )
            if result.get("status") == "ok":
                available += 1
            else:
                unavailable += 1
            logs.append(
                {
                    "race": race_no,
                    "stage": "live",
                    "calculatedAt": calculated_at,
                    "deadline": race.get("deadline"),
                    "status": result.get("status"),
                    "reason": result.get("reason"),
                    "morningProvisionalFlyProbability": morning_probability,
                    "legacyFlyProbability": (result.get("comparisonLog") or {}).get("legacyFlyProbability"),
                    "v3FlyProbability": (result.get("comparisonLog") or {}).get("v3FlyProbability"),
                    "tide": result.get("tide"),
                }
            )

        active = race.get("prediction")
        if isinstance(active, dict):
            selected = final if active.get("phase") == "final" and isinstance(final, dict) else pre
            if isinstance(selected, dict):
                race["prediction"] = deepcopy(selected)

    staged["preds"] = {
        str(int(race.get("race") or 0)): race.get("prediction")
        for race in staged.get("races") or []
    }
    staged["staging"] = {
        "enabled": True,
        "productionPublished": False,
        "model": MODEL_NAME,
        "morningMethod": MORNING_PROVISIONAL_MODEL_NAME,
        "morningIsV3": False,
        "generatedAt": calculated_at,
    }
    report = {
        "date": target_date,
        "venue": "wakamatsu",
        "model": MODEL_NAME,
        "available": available,
        "unavailable": unavailable,
        "liveNotReady": live_not_ready,
        "morningAvailable": morning_available,
        "morningUnavailable": morning_unavailable,
        "races": logs,
    }
    return staged, report


def run_staging(
    target_date: str,
    data_root: Path,
    output_root: Path,
    *,
    tide_provider: Callable[[str], dict[str, Any]] = fetch_validated_tide,
) -> dict[str, Any]:
    date.fromisoformat(target_date)
    source_path = data_root / "venues" / "wakamatsu" / f"{target_date.replace('-', '')}.json"
    source = load_json(source_path)
    tide_payload = tide_provider(target_date)
    staged, report = build_staging_payload(source, target_date, tide_payload)
    destination = output_root / "venues" / "wakamatsu" / f"{target_date.replace('-', '')}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(staged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest = destination.with_name("latest.json")
    latest.write_text(destination.read_text(encoding="utf-8"), encoding="utf-8")
    report["sourcePath"] = str(source_path)
    report["stagingPath"] = str(destination)
    report["productionWritten"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-root", default="staging/data")
    args = parser.parse_args()
    report = run_staging(
        args.date,
        (Path(args.data_root) if Path(args.data_root).is_absolute() else REPO_ROOT / args.data_root).resolve(),
        (Path(args.output_root) if Path(args.output_root).is_absolute() else REPO_ROOT / args.output_root).resolve(),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
