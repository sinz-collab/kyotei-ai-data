from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path


def load_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def complete_data(path: Path) -> dict | None:
    document = load_json(path)
    if not document:
        return None
    if document.get("complete") is not True:
        return None
    if document.get("status") != "complete":
        return None
    data = document.get("data")
    return data if isinstance(data, dict) else None


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                indent=2,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def apply_live_fields(
    payload: dict,
    race_no: int,
    live_root: Path,
) -> dict:
    race = next(
        (
            item
            for item in payload.get("races") or []
            if int(item.get("race") or 0) == race_no
        ),
        None,
    )

    if race is None:
        raise RuntimeError(f"race_not_found: {race_no}")

    # Older versions duplicated live data at both race root and race["live"].
    # Keep one canonical location under race["live"].
    for legacy_key in (
        "direct",
        "exhibition",
        "original",
        "original_exhibition",
    ):
        race.pop(legacy_key, None)

    direct = complete_data(live_root / "direct.json")
    exhibition = complete_data(live_root / "exhibition.json")
    original = complete_data(
        live_root / "original_exhibition.json"
    )
    odds = complete_data(live_root / "odds.json")
    result = complete_data(live_root / "result.json")

    live = race.get("live")
    if not isinstance(live, dict):
        live = {}

    if direct is not None:
        live["direct"] = direct
        live["weather"] = direct

        for key in (
            "actual_entry",
            "entry_changed",
            "withdrawals",
            "stabilizer",
            "lap_shortened",
            "other_changes",
            "racers",
            "weather",
            "air_temperature",
            "water_temperature",
            "wind_direction",
            "wind_speed",
            "wave_height",
        ):
            if key in direct:
                live[key] = direct[key]

        actual_entry = direct.get("actual_entry")

        if isinstance(actual_entry, list) and len(actual_entry) == 6:
            course_by_lane = {
                int(lane): course
                for course, lane in enumerate(actual_entry, start=1)
            }

            for collection_name in ("racers", "entries"):
                collection = race.get(collection_name)
                if not isinstance(collection, list):
                    continue

                for racer in collection:
                    if not isinstance(racer, dict):
                        continue

                    try:
                        lane = int(racer.get("lane"))
                    except (TypeError, ValueError):
                        continue

                    actual_course = course_by_lane.get(lane)
                    if actual_course is not None:
                        racer["actual_course"] = actual_course

            race["entry_changes"] = [
                {
                    "lane": lane,
                    "from": lane,
                    "to": course,
                }
                for lane, course in sorted(course_by_lane.items())
                if lane != course
            ]

    if exhibition is not None:
        live["exhibition"] = exhibition

    if original is not None:
        live["original"] = original
        live["original_exhibition"] = original

    if odds is not None:
        race["odds"] = odds
        live["odds"] = odds

    if result is not None:
        race["result"] = result
        live["result"] = result

    race["live"] = live
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--race", required=True, type=int)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--live-root", default=None)
    args = parser.parse_args()

    data_root = Path(args.data_root)
    date_key = args.date.replace("-", "")

    dated_path = (
        data_root
        / "venues"
        / "ashiya"
        / f"{date_key}.json"
    )
    latest_path = (
        data_root
        / "venues"
        / "ashiya"
        / "latest.json"
    )

    live_root = (
        Path(args.live_root)
        if args.live_root
        else data_root
        / "live"
        / args.date
        / "ashiya"
        / f"{args.race:02d}"
    )

    if not dated_path.is_file():
        raise FileNotFoundError(dated_path)

    payload = json.loads(
        dated_path.read_text(encoding="utf-8")
    )

    payload = apply_live_fields(
        payload,
        args.race,
        live_root,
    )

    atomic_write_json(dated_path, payload)
    atomic_write_json(latest_path, payload)

    print(
        json.dumps(
            {
                "date": args.date,
                "venue": "ashiya",
                "race": args.race,
                "liveRoot": str(live_root),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
