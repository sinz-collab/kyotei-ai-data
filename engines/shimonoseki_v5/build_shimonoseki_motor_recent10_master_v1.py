from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Iterable

GRADE_RE = re.compile(r"^[AB][12]$")
TIME_RE = re.compile(r"^\d+\.\d+$")
RANK_RE = re.compile(r"^(\d+(?:\.\d+)?)位$")
LANE_RE = re.compile(r"^[1-6]$")
MOTOR_RE = re.compile(r"^No\.\s*(\d+)$")
NORMAL_FINISH_RE = re.compile(r"^[1-6]$")
SPECIAL_FINISHES = {"転", "妨", "失", "欠", "Ｆ", "F", "エ", "落", "不", "L", "妨害"}

LONG_WEIGHTS = {
    "win_rate": 7.0,
    "top2_rate": 9.0,
    "top3_rate": 9.0,
    "avg_exhibition_time": 4.0,
    "deashi_score": 2.0,
    "nobi_score": 2.0,
    "mawariashi_score": 2.0,
}
RECENT_WEIGHTS = {
    "top2_rate": 11.0,
    "top3_rate": 12.0,
    "avg_exhibition_rank": 7.0,
    "finish_quality": 6.0,
    "trend": 4.0,
}
RIDER_WEIGHT = 15.0
COURSE_WEIGHT = 10.0

# Fixed v1 absolute norms. These do NOT depend on the day's field.
FIXED_NORMS_V1 = {
    "version": "shimonoseki_motor_recent10_norms_v1",
    "recent_top2_good": 0.60,
    "recent_top3_good": 0.75,
    "exhibition_rank_best": 1.0,
    "exhibition_rank_worst": 6.0,
    "trend_delta_span": 1.50,
    "grade_difficulty": {"A1": 0.90, "A2": 1.00, "B1": 1.10, "B2": 1.20},
    "course_difficulty": {"1": 0.85, "2": 0.95, "3": 1.00, "4": 1.05, "5": 1.10, "6": 1.15},
    "rank_thresholds": {"S": 85.0, "A": 72.0, "B": 58.0, "C": 45.0},
    "accident_policy": "special finishes excluded from finish-quality denominators",
    "motor_type_policy": "legacy motor_type label is not used in v1 scoring; numeric foot scores are used directly",
    "schedule_policy": {
        "day1": "motor_recent10 strong; setsukan absent/low",
        "day2": "motor_recent10 medium-high; combine with day1 setsukan",
        "day3": "motor_recent10 medium; prioritize setsukan and scenario BD",
        "semifinal": "motor_recent10 supporting; prioritize reproducible setsukan/real-race foot/scenario BD",
        "final": "motor_recent10 supporting-low; prioritize setsukan stability/course/scenario BD",
    },
}

HISTORY_FIELDS = [
    "snapshot_date", "motor_no", "runs_count", "valid_finish_count",
    "recent_top2_rate", "recent_top3_rate", "avg_exhibition_time", "avg_exhibition_rank",
    "finish_quality", "trend_delta", "rider_adjusted_quality", "course_adjusted_quality",
    "course_diversity", "source_hash", "runs_json",
]

MASTER_FIELDS = [
    "motor_no", "snapshot_date", "long_n", "long_win_rate", "long_top2_rate", "long_top3_rate",
    "long_avg_exhibition_time", "deashi_score", "nobi_score", "mawariashi_score",
    "recent_runs_count", "recent_valid_finish_count", "recent_top2_rate", "recent_top3_rate",
    "recent_avg_exhibition_time", "recent_avg_exhibition_rank", "recent_finish_quality", "recent_trend_delta",
    "rider_adjusted_quality", "course_adjusted_quality", "course_diversity",
    "long_score_35", "recent_score_40", "rider_score_15", "course_score_10", "motor_score_100",
    "motor_rank", "reliability", "normalization_version", "source",
]


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def fnum(v, default=None):
    try:
        if v is None or str(v).strip() == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def finish_number(v):
    m = re.match(r"^([1-6])", str(v or "").strip())
    return int(m.group(1)) if m else None


def finish_quality_value(finish: object) -> float | None:
    n = finish_number(finish)
    if n is None:
        return None
    return {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2, 6: 0.0}[n]


def read_nonempty(path: Path) -> list[str]:
    return [x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]


def parse_run(lines: list[str], index: int):
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


def parse_motor_recent_text(path: Path) -> dict[int, dict]:
    lines = read_nonempty(path)
    try:
        start = lines.index("モーター直近10走")
    except ValueError:
        return {}
    try:
        end = lines.index("モーター直近10走について", start + 1)
    except ValueError:
        end = len(lines)
    section = lines[start:end]
    starts = []
    for i in range(len(section) - 1):
        if LANE_RE.fullmatch(section[i]):
            mm = MOTOR_RE.fullmatch(section[i + 1])
            if mm:
                starts.append((i, int(section[i]), mm.group(1)))
    result = {}
    for pos, (st, lane, motor_no) in enumerate(starts[:6]):
        en = starts[pos + 1][0] if pos + 1 < len(starts) else len(section)
        block = section[st:en]
        cursor = 2
        for label in ("レーサー", "展示タイム/順位", "進入 / 着順"):
            if cursor < len(block) and block[cursor] == label:
                cursor += 1
        runs = []
        while cursor < len(block) and len(runs) < 10:
            run, nxt = parse_run(block, cursor)
            if run:
                runs.append(run)
                cursor = nxt
            else:
                cursor += 1
        result[lane] = {"motor_no": motor_no, "runs": runs}
    return result


def aggregate_runs(motor_no: str, runs: list[dict], snapshot_date: str) -> dict:
    valid = [(r, finish_quality_value(r.get("finish"))) for r in runs]
    valid = [(r, q) for r, q in valid if q is not None]
    nums = [finish_number(r.get("finish")) for r, _ in valid]
    top2 = sum(1 for n in nums if n <= 2) / len(nums) if nums else 0.0
    top3 = sum(1 for n in nums if n <= 3) / len(nums) if nums else 0.0
    times = [fnum(r.get("exhibition_time")) for r in runs]
    times = [x for x in times if x is not None]
    ranks = [fnum(r.get("exhibition_rank")) for r in runs]
    ranks = [x for x in ranks if x is not None]
    finish_q = mean([q for _, q in valid]) if valid else 0.0

    # BOATERS order is most recent first. Trend > 0 means recent five are better.
    qseq = [finish_quality_value(r.get("finish")) for r in runs]
    qseq = [q for q in qseq if q is not None]
    trend_delta = 0.0
    if len(qseq) >= 6:
        split = min(5, len(qseq) // 2)
        recent = qseq[:split]
        older = qseq[split:]
        if older:
            trend_delta = mean(recent) - mean(older)

    grade_mult = FIXED_NORMS_V1["grade_difficulty"]
    rider_vals = []
    for r, q in valid:
        rider_vals.append(clamp(q * grade_mult.get(str(r.get("class")), 1.0)))
    rider_q = mean(rider_vals) if rider_vals else 0.0

    course_mult = FIXED_NORMS_V1["course_difficulty"]
    course_vals = []
    courses = set()
    for r, q in valid:
        c = str(r.get("course"))
        courses.add(c)
        course_vals.append(clamp(q * course_mult.get(c, 1.0)))
    course_q = mean(course_vals) if course_vals else 0.0
    diversity = len(courses) / 6.0 if courses else 0.0

    source_blob = json.dumps(runs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "snapshot_date": snapshot_date,
        "motor_no": str(motor_no),
        "runs_count": len(runs),
        "valid_finish_count": len(valid),
        "recent_top2_rate": round(top2, 6),
        "recent_top3_rate": round(top3, 6),
        "avg_exhibition_time": round(mean(times), 4) if times else "",
        "avg_exhibition_rank": round(mean(ranks), 4) if ranks else "",
        "finish_quality": round(finish_q, 6),
        "trend_delta": round(trend_delta, 6),
        "rider_adjusted_quality": round(rider_q, 6),
        "course_adjusted_quality": round(course_q, 6),
        "course_diversity": round(diversity, 6),
        "source_hash": hashlib.sha256(source_blob).hexdigest()[:16],
        "runs_json": json.dumps(runs, ensure_ascii=False, separators=(",", ":")),
    }


def collect_snapshot(raw_dir: Path, snapshot_date: str) -> dict[str, dict]:
    by_motor: dict[str, dict] = {}
    for path in sorted(raw_dir.glob("race_*_motor.txt")):
        for item in parse_motor_recent_text(path).values():
            motor_no = str(item["motor_no"])
            agg = aggregate_runs(motor_no, item["runs"], snapshot_date)
            old = by_motor.get(motor_no)
            # Prefer the most complete parse; same motor may appear in multiple races.
            if old is None or int(agg["runs_count"]) > int(old["runs_count"]):
                by_motor[motor_no] = agg
    return by_motor


def load_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, fields: list[str], rows: Iterable[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def append_history(history_path: Path, new_rows: dict[str, dict]):
    existing = load_csv(history_path)
    keyset = {(r.get("snapshot_date"), r.get("motor_no"), r.get("source_hash")) for r in existing}
    for row in new_rows.values():
        key = (str(row["snapshot_date"]), str(row["motor_no"]), str(row["source_hash"]))
        if key not in keyset:
            existing.append(row)
            keyset.add(key)
    existing.sort(key=lambda r: (r.get("snapshot_date", ""), int(r.get("motor_no") or 0)))
    write_csv(history_path, HISTORY_FIELDS, existing)
    return existing


def percentile_factor(values: list[float], value: float, lower_is_better=False) -> float:
    vals = sorted(v for v in values if v is not None and math.isfinite(v))
    if not vals:
        return 0.5
    if len(vals) == 1:
        return 0.5
    lt = sum(1 for v in vals if v < value)
    eq = sum(1 for v in vals if v == value)
    pct = (lt + 0.5 * eq) / len(vals)
    return 1.0 - pct if lower_is_better else pct


def long_score(long_rows: list[dict], row: dict) -> float:
    total = 0.0
    for field, weight in LONG_WEIGHTS.items():
        value = fnum(row.get(field))
        vals = [fnum(r.get(field)) for r in long_rows]
        vals = [v for v in vals if v is not None]
        if value is None:
            factor = 0.5
        else:
            factor = percentile_factor(vals, value, lower_is_better=(field == "avg_exhibition_time"))
        total += weight * factor
    return total


def recent_score(snap: dict) -> float:
    t2 = fnum(snap.get("recent_top2_rate"), 0.0)
    t3 = fnum(snap.get("recent_top3_rate"), 0.0)
    ex_rank = fnum(snap.get("avg_exhibition_rank"), 3.5)
    fq = fnum(snap.get("finish_quality"), 0.0)
    trend = fnum(snap.get("trend_delta"), 0.0)
    factor_t2 = clamp(t2 / FIXED_NORMS_V1["recent_top2_good"])
    factor_t3 = clamp(t3 / FIXED_NORMS_V1["recent_top3_good"])
    factor_rank = clamp((FIXED_NORMS_V1["exhibition_rank_worst"] - ex_rank) /
                        (FIXED_NORMS_V1["exhibition_rank_worst"] - FIXED_NORMS_V1["exhibition_rank_best"]))
    factor_trend = clamp(0.5 + trend / (2 * FIXED_NORMS_V1["trend_delta_span"]))
    return (
        RECENT_WEIGHTS["top2_rate"] * factor_t2
        + RECENT_WEIGHTS["top3_rate"] * factor_t3
        + RECENT_WEIGHTS["avg_exhibition_rank"] * factor_rank
        + RECENT_WEIGHTS["finish_quality"] * clamp(fq)
        + RECENT_WEIGHTS["trend"] * factor_trend
    )


def rank_for(score: float) -> str:
    t = FIXED_NORMS_V1["rank_thresholds"]
    if score >= t["S"]: return "S"
    if score >= t["A"]: return "A"
    if score >= t["B"]: return "B"
    if score >= t["C"]: return "C"
    return "D"


def build_master(long_master_path: Path, history_rows: list[dict], out_path: Path):
    long_rows = load_csv(long_master_path)
    by_long = {str(r["motor_no"]): r for r in long_rows}
    latest = {}
    for r in history_rows:
        m = str(r.get("motor_no"))
        if m not in latest or r.get("snapshot_date", "") >= latest[m].get("snapshot_date", ""):
            latest[m] = r
    rows = []
    for motor_no, snap in sorted(latest.items(), key=lambda kv: int(kv[0])):
        base = by_long.get(motor_no)
        if not base:
            continue
        ls = long_score(long_rows, base)
        rs = recent_score(snap)
        rider = RIDER_WEIGHT * clamp(fnum(snap.get("rider_adjusted_quality"), 0.0))
        cq = clamp(fnum(snap.get("course_adjusted_quality"), 0.0))
        div = clamp(fnum(snap.get("course_diversity"), 0.0))
        cs = COURSE_WEIGHT * (0.8 * cq + 0.2 * div)
        total = ls + rs + rider + cs
        valid = int(float(snap.get("valid_finish_count") or 0))
        runs = int(float(snap.get("runs_count") or 0))
        sample_rel = fnum(base.get("sample_reliability"), 1.0)
        reliability = clamp((valid / 10.0) * (sample_rel if sample_rel is not None else 1.0))
        rows.append({
            "motor_no": motor_no,
            "snapshot_date": snap.get("snapshot_date"),
            "long_n": base.get("n"),
            "long_win_rate": base.get("win_rate"),
            "long_top2_rate": base.get("top2_rate"),
            "long_top3_rate": base.get("top3_rate"),
            "long_avg_exhibition_time": base.get("avg_exhibition_time"),
            "deashi_score": base.get("deashi_score"),
            "nobi_score": base.get("nobi_score"),
            "mawariashi_score": base.get("mawariashi_score"),
            "recent_runs_count": runs,
            "recent_valid_finish_count": valid,
            "recent_top2_rate": snap.get("recent_top2_rate"),
            "recent_top3_rate": snap.get("recent_top3_rate"),
            "recent_avg_exhibition_time": snap.get("avg_exhibition_time"),
            "recent_avg_exhibition_rank": snap.get("avg_exhibition_rank"),
            "recent_finish_quality": snap.get("finish_quality"),
            "recent_trend_delta": snap.get("trend_delta"),
            "rider_adjusted_quality": snap.get("rider_adjusted_quality"),
            "course_adjusted_quality": snap.get("course_adjusted_quality"),
            "course_diversity": snap.get("course_diversity"),
            "long_score_35": round(ls, 3),
            "recent_score_40": round(rs, 3),
            "rider_score_15": round(rider, 3),
            "course_score_10": round(cs, 3),
            "motor_score_100": round(total, 3),
            "motor_rank": rank_for(total),
            "reliability": round(reliability, 3),
            "normalization_version": FIXED_NORMS_V1["version"],
            "source": "boaters_motor_recent10+shimonoseki_motor_type_master_v1",
        })
    write_csv(out_path, MASTER_FIELDS, rows)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--raw-dir", required=True, type=Path, help="Directory containing race_XX_motor.txt")
    ap.add_argument("--long-master", required=True, type=Path)
    ap.add_argument("--history-out", required=True, type=Path)
    ap.add_argument("--master-out", required=True, type=Path)
    ap.add_argument("--norms-out", type=Path)
    args = ap.parse_args()

    snapshot = collect_snapshot(args.raw_dir, args.date)
    history = append_history(args.history_out, snapshot)
    rows = build_master(args.long_master, history, args.master_out)
    if args.norms_out:
        args.norms_out.write_text(json.dumps(FIXED_NORMS_V1, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    dist = {k: 0 for k in "SABCD"}
    for r in rows:
        dist[r["motor_rank"]] += 1
    print(json.dumps({
        "date": args.date,
        "snapshot_motors": len(snapshot),
        "history_rows": len(history),
        "master_rows": len(rows),
        "rank_distribution": dist,
        "top10": [
            {"motor_no": r["motor_no"], "score": r["motor_score_100"], "rank": r["motor_rank"]}
            for r in sorted(rows, key=lambda x: float(x["motor_score_100"]), reverse=True)[:10]
        ],
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
