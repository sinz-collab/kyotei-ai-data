#!/usr/bin/env python3
"""Fill missing official-stage predictions using each venue's existing engine.

Run engines in isolated temporary data directories and merge only predictions
for races whose deadline has not passed. Never rewrite existing LIVE/history.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from datetime import date as calendar_date
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from .course_st_3m import CourseSt3m
except ImportError:
    from course_st_3m import CourseSt3m

COMMANDS = {
    "kiryu": ("run_kiryu_v1.py", "--stage", "morning"),
    "toda": ("apply_toda_v5.py",),
    "heiwajima": ("apply_heiwajima_v1.py",),
    "biwako": ("run_biwako_v1_2.py", "--stage", "preliminary"),
    "naruto": ("run_naruto_v5.py", "--stage", "morning"),
    "ashiya": ("run_ashiya_v16.py", "--stage", "pre"),
    "fukuoka": ("run_fukuoka_v1.py", "--stage", "preliminary"),
    "omura": ("apply_omura_v1.py",),
    "shimonoseki": ("run_shimonoseki_v6_1.py", "--stage", "preliminary"),
    "wakamatsu": ("apply_wakamatsu_v2_3.py",),
    "karatsu": ("run_karatsu_official.py",),
}

SUPPLEMENT_FIELDS = (
    "boaters_local_avg_st", "boaters_local_st_rank", "motor_recent",
    "boaters_kimarite_starts", "boaters_escape_rate", "boaters_sashare_rate",
    "boaters_makurare_rate", "boaters_makurare_zashi_rate", "boaters_nigashi_rate",
    "boaters_sashi_rate", "boaters_makuri_rate", "boaters_makuri_sashi_rate",
)


def supplement_hash(race: dict) -> str:
    rows = [{key: racer.get(key) for key in SUPPLEMENT_FIELDS if key in racer}
            for racer in race.get("racers") or []]
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def still_open(payload: dict, race: dict, now: datetime) -> bool:
    try:
        cutoff = datetime.strptime(
            f"{payload['date']} {race['deadline']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    except (ValueError, KeyError):
        return False
    return now < cutoff


def missing_races(payload: dict, now: datetime) -> set[int]:
    existing = payload.get("preds") or {}
    result = set()
    for race in payload.get("races") or []:
        number = int(race.get("race") or 0)
        if not number or existing.get(str(number)) or race.get("prediction"):
            continue
        if still_open(payload, race, now):
            result.add(number)
    return result


def supplement_races(payload: dict, now: datetime) -> set[int]:
    if (payload.get("sourceStatus") or {}).get("official_morning", {}).get("status") != "published_supplement":
        return set()
    preds = payload.get("preds") or {}
    result = set()
    for race in payload.get("races") or []:
        number = int(race.get("race") or 0)
        old = preds.get(str(number)) or {}
        if not number or not still_open(payload, race, now):
            continue
        # Only stage-one forecasts carrying a source hash may be recalculated.
        if not old.get("officialSupplementHash") or old.get("officialPredictionStage") == "supplemented":
            continue
        if old.get("prediction_history", {}).get("live") or old.get("active_prediction_stage") == "live":
            continue
        stage = old.get("predictionStage") or {}
        if isinstance(stage, dict) and stage.get("label") == "本予想":
            continue
        if str(stage).lower() in ("live", "final"):
            continue
        if supplement_hash(race) != old["officialSupplementHash"]:
            result.add(number)
    return result


def merge_missing(original: dict, generated: dict, eligible: set[int]) -> tuple[dict, int]:
    merged = copy.deepcopy(original)
    originals = {int(row.get("race") or 0): row for row in merged.get("races") or []}
    created = 0
    for number in sorted(eligible):
        race = originals.get(number)
        source = next((row for row in generated.get("races") or [] if int(row.get("race") or 0) == number), {})
        new_prediction = (generated.get("preds") or {}).get(str(number))
        if not new_prediction and not source.get("prediction"):
            continue
        if new_prediction:
            stamped = copy.deepcopy(new_prediction)
            stamped["officialSupplementHash"] = supplement_hash(race)
            stamped["officialPredictionStage"] = "preliminary"
            merged.setdefault("preds", {})[str(number)] = stamped
        for key in ("prediction", "predictionPre", "predictionFinal"):
            if key in source and key not in race:
                race[key] = copy.deepcopy(source[key])
        created += 1
    if created:
        complete = all(
            (merged.get("preds") or {}).get(str(row.get("race"))) or row.get("prediction")
            for row in merged.get("races") or []
        ) and len(merged.get("races") or []) == 12
        merged["predictionStatus"] = "ready" if complete else "partial"
        merged["predictionReason"] = "" if merged["predictionStatus"] == "ready" else "pending_predictions"
        if generated.get("engine"):
            merged["engine"] = generated["engine"]
        if generated.get("engineVersion"):
            merged["engineVersion"] = generated["engineVersion"]
    return merged, created


def merge_supplement(original: dict, generated: dict, eligible: set[int]) -> tuple[dict, int]:
    merged = copy.deepcopy(original)
    races = {int(row.get("race") or 0): row for row in merged.get("races") or []}
    generated_races = {int(row.get("race") or 0): row for row in generated.get("races") or []}
    corrected = 0
    for number in sorted(eligible):
        old = (merged.get("preds") or {}).get(str(number))
        new = (generated.get("preds") or {}).get(str(number))
        if not old or not new:
            continue
        updated = copy.deepcopy(new)
        # Preserve the existing history; never rewrite an original morning or LIVE snapshot.
        if "prediction_history" in old:
            updated["prediction_history"] = copy.deepcopy(old["prediction_history"])
        updated["officialSupplementHash"] = supplement_hash(races[number])
        updated["officialPredictionStage"] = "supplemented"
        updated["officialPreliminarySummary"] = {
            key: copy.deepcopy(old[key]) for key in ("win", "second", "third", "tickets") if key in old
        }
        merged["preds"][str(number)] = updated
        current_race = races[number]
        new_race = generated_races.get(number, {})
        for key in ("prediction", "predictionPre"):
            if key in new_race and key in current_race:
                current_race[key] = copy.deepcopy(new_race[key])
        corrected += 1
    return merged, corrected


def apply_official_course_st(race_payload: dict, model: CourseSt3m) -> int:
    """Replace engine-local ST in an isolated input; never alter published racers."""
    if not model.complete:
        return 0
    count = 0
    for race in race_payload.get("races") or []:
        for racer in race.get("racers") or []:
            reg = racer.get("player_id") or racer.get("reg_no") or racer.get("registration_number")
            course = (racer.get("actual_course") or racer.get("entry_course")
                      or racer.get("course") or racer.get("lane"))
            try:
                selected = model.select(reg, int(course)) if reg and course else None
            except (TypeError, ValueError):
                selected = None
            # Never silently fall back to the unrelated BOATERS local-ST metric.
            value = (f"{selected['average_st']:.2f}" if selected and
                     selected["average_st"] is not None else "")
            racer["local_st"] = value
            racer["boaters_local_avg_st"] = value
            racer["official_course_st_3m"] = selected or {
                "status": "unavailable", "average_st": None,
                "reason": "racer_or_course_missing",
            }
            count += bool(value)
    return count


def run(date: str, data_root: Path, now: datetime, venues: list[str], stage: str = "official",
        course_db: Path | None = None) -> dict:
    from official_morning import atomic_json
    from publish_official_stages import update_index_files
    from build_site_data import prediction_payload_gate

    published = {}
    results = {"date": date, "stage": stage, "venues": {}, "generated": 0,
               "courseSt3mCoverage": {}}
    model = CourseSt3m(course_db, calendar_date.fromisoformat(date)) if course_db else None
    results["officialCourseSt3m"] = {
        "status": "ready" if model and model.complete else "unavailable",
        "reason": model.reason if model else "not_configured",
    }
    compact = date.replace("-", "")
    for slug in venues:
        path = data_root / "venues" / slug / f"{compact}.json"
        if not path.is_file():
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        if original.get("date") != date:
            continue
        eligible = missing_races(original, now) if stage == "official" else supplement_races(original, now)
        if not eligible:
            results["venues"][slug] = "no_eligible_races"
            continue
        with tempfile.TemporaryDirectory(prefix=f"official-pred-{slug}-") as directory:
            isolated = Path(directory)
            target = isolated / "venues" / slug / f"{compact}.json"
            target.parent.mkdir(parents=True)
            isolated_payload = copy.deepcopy(original)
            if model and model.complete:
                applied = apply_official_course_st(isolated_payload, model)
                results["courseSt3mCoverage"][slug] = applied
            atomic_json(target, isolated_payload)
            script, *flags = COMMANDS[slug]
            command = [sys.executable, str(Path(__file__).parent / script),
                       "--date", date, "--data-root", str(isolated), *flags]
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
                generated = json.loads(target.read_text(encoding="utf-8"))
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError) as exc:
                results["venues"][slug] = f"engine_failed:{type(exc).__name__}"
                if isinstance(exc, subprocess.CalledProcessError):
                    results.setdefault("engineErrors", {})[slug] = (exc.stderr or exc.stdout or "")[-1500:]
                continue
        valid, reason = prediction_payload_gate(generated, date)
        if not valid:
            results["venues"][slug] = f"prediction_invalid:{reason}"
            continue
        # An engine may have a broader output contract. Only copy missing predictions.
        merge = merge_missing if stage == "official" else merge_supplement
        merged, count = merge(original, generated, eligible)
        if count:
            atomic_json(path, merged)
            latest = path.parent / "latest.json"
            if latest.is_file():
                latest_payload = json.loads(latest.read_text(encoding="utf-8"))
                if latest_payload.get("date") == date:
                    # Re-evaluate against the latest LIVE state before writing anything.
                    latest_eligible = (missing_races(latest_payload, now) if stage == "official"
                                       else supplement_races(latest_payload, now)) & eligible
                    updated_latest, _ = merge(latest_payload, generated, latest_eligible)
                    atomic_json(latest, updated_latest)
            published[slug] = merged
        results["venues"][slug] = count
        results["generated"] += count
    if published:
        update_index_files(data_root, date, published)
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--stage", choices=("official", "supplement"), default="official")
    parser.add_argument("--data-root", type=Path, default=Path("data"))
    parser.add_argument("--course-db", type=Path)
    parser.add_argument("--venues", nargs="*", choices=sorted(COMMANDS), default=sorted(COMMANDS))
    args = parser.parse_args()
    now = datetime.now(ZoneInfo("Asia/Tokyo"))
    if args.date != now.date().isoformat():
        raise SystemExit("Only JST today is eligible")
    print(json.dumps(run(args.date, args.data_root, now, args.venues, args.stage, args.course_db), ensure_ascii=False))


if __name__ == "__main__":
    main()
