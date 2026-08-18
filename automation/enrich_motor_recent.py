from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent

GRADE_RE = re.compile(r"^[AB][12]$")
TIME_RE = re.compile(r"^\d+\.\d+$")
RANK_RE = re.compile(r"^(\d+(?:\.\d+)?)位$")
LANE_RE = re.compile(r"^[1-6]$")
MOTOR_RE = re.compile(r"^No\.\s*(\d+)$")
NORMAL_FINISH_RE = re.compile(r"^[1-6]$")
SPECIAL_FINISHES = {"転", "妨", "失", "欠", "Ｆ", "F", "エ", "落", "不"}


def read_nonempty(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]


def as_float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def finish_number(value: object) -> int | None:
    match = re.match(r"^([1-6])", str(value or "").strip())
    return int(match.group(1)) if match else None


def classify_trend(finishes: list[object], ranks: list[float]) -> str:
    """Display-only trend label. Recent half vs older half; lower is better."""
    finish_scores = [finish_number(value) for value in finishes]
    numeric = [value if value is not None else 7 for value in finish_scores]
    if len(numeric) < 6:
        return "unknown"
    split = max(3, len(numeric) // 2)
    recent = numeric[:split]
    older = numeric[split:]
    if not older:
        return "unknown"
    delta = mean(older) - mean(recent)
    if ranks and len(ranks) >= 6:
        delta += (mean(ranks[split:]) - mean(ranks[:split])) * 0.35
    if delta >= 0.65:
        return "up"
    if delta <= -0.65:
        return "down"
    return "flat"


def parse_run(lines: list[str], index: int) -> tuple[dict | None, int]:
    if index + 5 >= len(lines) or not GRADE_RE.fullmatch(lines[index + 1]):
        return None, index + 1
    if not TIME_RE.fullmatch(lines[index + 2]):
        return None, index + 1
    rank_match = RANK_RE.fullmatch(lines[index + 3])
    if not rank_match or not LANE_RE.fullmatch(lines[index + 4]):
        return None, index + 1
    finish = lines[index + 5]
    if not (NORMAL_FINISH_RE.fullmatch(finish) or finish in SPECIAL_FINISHES):
        return None, index + 1
    next_index = index + 6
    if next_index < len(lines) and lines[next_index] == "着":
        next_index += 1
    return {
        "racer": lines[index],
        "class": lines[index + 1],
        "exhibition_time": float(lines[index + 2]),
        "exhibition_rank": float(rank_match.group(1)),
        "course": int(lines[index + 4]),
        "finish": finish,
    }, next_index


def summarize_motor(
    lane: int,
    motor_no: str,
    runs: list[dict],
    avg_time: float | None,
    avg_rank: float | None,
) -> dict:
    finish_numbers = [finish_number(run.get("finish")) for run in runs]
    valid_finishes = [value for value in finish_numbers if value is not None]
    top2 = sum(1 for value in valid_finishes if value <= 2)
    top3 = sum(1 for value in valid_finishes if value <= 3)
    ranks = [
        float(run["exhibition_rank"])
        for run in runs
        if as_float(run.get("exhibition_rank")) is not None
    ]
    if avg_time is None:
        times = [
            float(run["exhibition_time"])
            for run in runs
            if as_float(run.get("exhibition_time")) is not None
        ]
        avg_time = round(mean(times), 3) if times else None
    if avg_rank is None and ranks:
        avg_rank = round(mean(ranks), 2)
    denominator = len(runs)
    return {
        "available": bool(runs),
        "lane": lane,
        "motor_no": motor_no,
        "runs_count": len(runs),
        "top2_rate": round(top2 * 100.0 / denominator, 1) if denominator else None,
        "top3_rate": round(top3 * 100.0 / denominator, 1) if denominator else None,
        "avg_exhibition_time": avg_time,
        "avg_exhibition_rank": avg_rank,
        "trend": classify_trend([run.get("finish") for run in runs], ranks),
        "finishes": [run.get("finish") for run in runs],
        "runs": runs,
        "source": "boaters_motor_recent10",
    }


def parse_motor_recent_text(path: Path) -> dict[int, dict]:
    lines = read_nonempty(path)
    try:
        section_start = lines.index("モーター直近10走")
    except ValueError:
        return {}
    try:
        section_end = lines.index("モーター直近10走について", section_start + 1)
    except ValueError:
        section_end = len(lines)
    section = lines[section_start:section_end]

    starts: list[tuple[int, int, str]] = []
    for index in range(len(section) - 1):
        if LANE_RE.fullmatch(section[index]):
            motor_match = MOTOR_RE.fullmatch(section[index + 1])
            if motor_match:
                starts.append((index, int(section[index]), motor_match.group(1)))

    parsed: dict[int, dict] = {}
    for position, (start, lane, motor_no) in enumerate(starts[:6]):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(section)
        block = section[start:end]
        cursor = 2
        for label in ("レーサー", "展示タイム/順位", "進入 / 着順"):
            if cursor < len(block) and block[cursor] == label:
                cursor += 1

        runs: list[dict] = []
        while cursor < len(block) and len(runs) < 10:
            run, next_cursor = parse_run(block, cursor)
            if run:
                runs.append(run)
                cursor = next_cursor
            else:
                cursor += 1

        avg_time = None
        avg_rank = None
        tail = block[cursor:]
        for index, token in enumerate(tail):
            if avg_time is None and TIME_RE.fullmatch(token):
                avg_time = float(token)
                if index + 1 < len(tail):
                    rank_match = RANK_RE.fullmatch(tail[index + 1])
                    if rank_match:
                        avg_rank = float(rank_match.group(1))
                break

        parsed[lane] = summarize_motor(lane, motor_no, runs, avg_time, avg_rank)

    return parsed


def load_venues(config_path: Path) -> list[dict]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return list(payload.get("venues") or [])


def enrich_date(
    date: str,
    repo_root: Path,
    data_root: Path | None = None,
    work_root: Path | None = None,
) -> dict:
    data_root = data_root or repo_root / "data"
    work_root = work_root or repo_root / "work" / "races"
    date_compact = date.replace("-", "")
    summary = {
        "date": date,
        "venues": {},
        "races_enriched": 0,
        "racers_enriched": 0,
    }

    for venue in load_venues(repo_root / "automation" / "venues.json"):
        slug = venue["slug"]
        name = venue["name"]
        public_path = data_root / "venues" / slug / f"{date_compact}.json"
        if not public_path.is_file():
            summary["venues"][slug] = {"status": "public_json_missing", "races": 0}
            continue

        try:
            payload = json.loads(public_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary["venues"][slug] = {"status": "public_json_invalid", "races": 0}
            continue

        changed = False
        races_done = 0
        racers_done = 0
        for race in payload.get("races") or []:
            race_no = int(race.get("race") or 0)
            source = work_root / name / date_compact / "races" / f"race_{race_no:02d}_motor.txt"
            if race_no < 1 or not source.is_file():
                continue

            motors = parse_motor_recent_text(source)
            if not motors:
                continue

            race["motorRecentAvailable"] = True
            race["motor_recent"] = [motors[lane] for lane in sorted(motors)]

            for racer in race.get("racers") or []:
                lane = int(racer.get("lane") or 0)
                item = motors.get(lane)
                if item:
                    racer["motor_recent"] = item
                    racers_done += 1

            for entry in race.get("entries") or []:
                lane = int(entry.get("lane") or 0)
                item = motors.get(lane)
                if item:
                    entry["motor_recent"] = item

            races_done += 1
            changed = True

        if changed:
            public_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            summary["races_enriched"] += races_done
            summary["racers_enriched"] += racers_done
            summary["venues"][slug] = {
                "status": "ok",
                "races": races_done,
                "racers": racers_done,
            }
        else:
            summary["venues"][slug] = {"status": "motor_source_missing", "races": 0}

    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--repo-root", default=str(HERE.parent))
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    result = enrich_date(args.date, repo_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
