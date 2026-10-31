from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

try:
    from .course_aptitude import CourseAptitudeEvaluator
except ImportError:  # Direct script execution in the morning workflow.
    from course_aptitude import CourseAptitudeEvaluator


REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = REPO_ROOT / "work" / "races"
VENUES_ROOT = REPO_ROOT / "data" / "venues"

VENUE_NAMES = {
    "kiryu": "桐生",
    "toda": "戸田",
    "edogawa": "江戸川",
    "heiwajima": "平和島",
    "tamagawa": "多摩川",
    "hamanako": "浜名湖",
    "gamagori": "蒲郡",
    "tokoname": "常滑",
    "tsu": "津",
    "mikuni": "三国",
    "biwako": "びわこ",
    "suminoe": "住之江",
    "amagasaki": "尼崎",
    "naruto": "鳴門",
    "marugame": "丸亀",
    "kojima": "児島",
    "miyajima": "宮島",
    "tokuyama": "徳山",
    "shimonoseki": "下関",
    "wakamatsu": "若松",
    "ashiya": "芦屋",
    "fukuoka": "福岡",
    "karatsu": "唐津",
    "omura": "大村",
    "biwako": "びわこ",
}


def normalize_name(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def starts_count(block: str) -> int | None:
    # BOATERS本文では各割合の回数が「（15回）」のように表示され、
    # 最後の出走回数だけが「123回」の単独行になる。
    # 次艇の表見出し「出走回数\n2」を誤取得しないよう、
    # 単独行の「数字+回」だけを対象にする。
    matches = list(
        re.finditer(
            r"(?m)^[ \t]*([0-9]+)[ \t]*回[ \t]*$",
            block,
        )
    )

    if not matches:
        return None

    return int(matches[-1].group(1))

def positional_rates(block: str) -> list[float | None]:
    tokens = re.findall(
        r"(?<!\S)-(?!\S)|[0-9]+(?:\.[0-9]+)?\s*%",
        block,
    )
    values: list[float | None] = []
    for token in tokens[:4]:
        token = token.strip()
        if token == "-":
            values.append(None)
        else:
            values.append(float(token.replace("%", "").strip()))
    while len(values) < 4:
        values.append(None)
    return values


def kimarite_section(text: str) -> str:
    start = text.find("決まり手率")

    if start < 0:
        return ""

    end_markers = (
        "決まり手率について",
        "AIオッズ評価",
        "前づけデータ",
    )

    end = len(text)

    for marker in end_markers:
        position = text.find(
            marker,
            start + len("決まり手率"),
        )
        if position >= 0:
            end = min(end, position)

    return text[start:end]


def bounded_section(text: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = len(text)
    for marker in end_markers:
        position = text.find(marker, start + len(start_marker))
        if position >= 0:
            end = min(end, position)
    return text[start:end]


def parse_recent_course_performance(
    text: str,
    racers: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Parse the selected BOATERS 6-month course block from the morning snapshot."""
    section = bounded_section(
        text,
        "AI3連対率・枠別勝率",
        ("AI3連対率の着順別表示", "枠別スタート順位"),
    )
    if not section or "直近6ヶ月" not in section:
        return {}
    parsed: dict[int, dict[str, Any]] = {}
    ordered = sorted(racers, key=lambda row: int(row.get("lane") or 0))
    for index, racer in enumerate(ordered):
        lane = int(racer.get("lane") or 0)
        if lane not in range(1, 7):
            continue
        next_name = ordered[index + 1].get("name") if index + 1 < len(ordered) else None
        block = racer_block(section, racer.get("name") or "", next_name)
        if not block:
            continue
        rates = [
            float(value)
            for value in re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*%", block)
        ]
        starts_match = re.search(r"\(\s*[0-9]+\s*/\s*([0-9]+)\s*回\s*\)", block)
        if len(rates) < 5 or not starts_match:
            continue
        starts = int(starts_match.group(1))
        parsed[lane] = {
            "starts": starts,
            "win_rate": rates[2],
            "top2_rate": round(rates[2] + rates[3], 1),
            "top3_rate": rates[1],
        }
    return parsed


def parse_recent_course_st(
    text: str,
    racers: list[dict[str, Any]],
) -> dict[int, float]:
    section = bounded_section(
        text,
        "枠別スタート順位",
        ("枠別スタート順位について", "決まり手率"),
    )
    if not section or "直近6ヶ月" not in section:
        return {}
    parsed: dict[int, float] = {}
    ordered = sorted(racers, key=lambda row: int(row.get("lane") or 0))
    for index, racer in enumerate(ordered):
        lane = int(racer.get("lane") or 0)
        next_name = ordered[index + 1].get("name") if index + 1 < len(ordered) else None
        block = racer_block(section, racer.get("name") or "", next_name)
        match = re.search(r"(?m)^\s*(0?\.[0-9]{2})\s*$", block)
        if lane in range(1, 7) and match:
            parsed[lane] = float(match.group(1))
    return parsed


def recent_course_record(
    racer: dict[str, Any],
    performance: dict[str, Any] | None,
    avg_st: float | None,
    kimarite: dict[str, Any] | None,
) -> dict[str, Any]:
    registration_no = str(racer.get("player_id") or racer.get("reg_no") or "").strip()
    course = int(
        racer.get("actual_course")
        or racer.get("entry_course")
        or racer.get("course")
        or racer.get("lane")
        or 0
    )
    base: dict[str, Any] = {
        "status": "insufficient",
        "period_months": 6,
        "registration_no": registration_no,
        "course": course if course in range(1, 7) else None,
        "source": "boaters_morning_snapshot",
    }
    if not registration_no or not performance or performance.get("starts", 0) <= 0:
        return base
    base.update(performance)
    if avg_st is not None:
        base["avg_st"] = avg_st
    if kimarite:
        base["kimarite"] = {
            key.removeprefix("boaters_"): value
            for key, value in kimarite.items()
            if key != "boaters_kimarite_starts"
        }
        base["kimarite_starts"] = kimarite.get("boaters_kimarite_starts")
    base["status"] = "available"
    return base


def racer_block(text: str, name: str, next_name: str | None) -> str:
    compact_name = normalize_name(name)
    if not compact_name:
        return ""
    name_pattern = r"[\s\u3000]*".join(map(re.escape, compact_name))
    start_match = re.search(name_pattern, text)
    if not start_match:
        return ""
    end = len(text)
    if next_name:
        compact_next = normalize_name(next_name)
        if compact_next:
            next_pattern = r"[\s\u3000]*".join(map(re.escape, compact_next))
            next_match = re.search(next_pattern, text[start_match.end():])
            if next_match:
                end = start_match.end() + next_match.start()
    return text[start_match.start():end]


def parse_race_kimarite(
    text: str,
    racers: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    text = kimarite_section(text)
    if not text:
        return {}
    parsed: dict[int, dict[str, Any]] = {}
    ordered = sorted(
        racers,
        key=lambda row: int(row.get("lane") or row.get("boatNumber") or 0),
    )
    for index, racer in enumerate(ordered):
        lane = int(racer.get("lane") or racer.get("boatNumber") or 0)
        if lane not in range(1, 7):
            continue
        next_name = ordered[index + 1].get("name") if index + 1 < len(ordered) else None
        block = racer_block(text, racer.get("name") or "", next_name)
        if not block:
            continue
        starts = starts_count(block)
        if starts is None:
            continue
        rates = positional_rates(block)
        row: dict[str, Any] = {"boaters_kimarite_starts": starts}
        if lane == 1:
            values = {
                "boaters_escape_rate": rates[0],
                "boaters_sashare_rate": rates[1],
                "boaters_makurare_rate": rates[2],
                "boaters_makurare_zashi_rate": rates[3],
            }
        else:
            values = {
                "boaters_nigashi_rate": rates[0],
                "boaters_sashi_rate": rates[1],
                "boaters_makuri_rate": rates[2],
                "boaters_makuri_sashi_rate": rates[3],
            }
        found = False
        for key, value in values.items():
            if value is not None:
                row[key] = value
                found = True
        parsed[lane] = row
    return parsed


def race_number_from_path(path: Path) -> int | None:
    match = re.search(
        r"(?:^|[^0-9])([0-9]{1,2})(?:R|_data|[^0-9]|$)",
        path.stem,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    race_no = int(match.group(1))
    return race_no if 1 <= race_no <= 12 else None


def path_matches_venue(path: Path, venue: str) -> bool:
    venue_lower = venue.lower()
    venue_name = VENUE_NAMES.get(venue, "")
    return any(
        part.lower() == venue_lower or (venue_name and part == venue_name)
        for part in path.parts
    )


def find_source_files(target_date: str, venue: str) -> dict[int, Path]:
    date_tokens = {target_date, target_date.replace("-", "")}
    candidates: dict[int, Path] = {}
    for path in WORK_ROOT.glob("**/*_data.txt"):
        if not path_matches_venue(path, venue):
            continue
        if not any(token in str(path) for token in date_tokens):
            continue
        race_no = race_number_from_path(path)
        if race_no is not None:
            candidates[race_no] = path
    return candidates


def enrich_payload(
    payload: dict[str, Any],
    sources: dict[int, Path],
) -> tuple[int, int, list[str]]:
    reflected = 0
    expected = 0
    warnings: list[str] = []
    for race in payload.get("races") or []:
        race_no = int(race.get("race") or race.get("race_no") or 0)
        racers = race.get("racers") or race.get("entries") or []
        expected += len(racers)
        source = sources.get(race_no)
        if not source:
            warnings.append(f"race_{race_no:02d}:kimarite_source_missing")
            continue
        source_text = source.read_text(encoding="utf-8", errors="replace")
        parsed = parse_race_kimarite(source_text, racers)
        by_lane = {
            int(row.get("lane") or row.get("boatNumber") or 0): row
            for row in racers
        }
        for lane, values in parsed.items():
            target = by_lane.get(lane)
            if target is None:
                continue
            target.update(values)
            reflected += 1
        if len(parsed) != len(racers):
            warnings.append(
                f"race_{race_no:02d}:kimarite_reflected_{len(parsed)}_of_{len(racers)}"
            )
    return reflected, expected, warnings


def target_payloads(
    target_date: str,
    selected_venues: set[str] | None,
) -> list[tuple[str, Path]]:
    compact_date = target_date.replace("-", "")
    targets: list[tuple[str, Path]] = []
    for venue_dir in sorted(path for path in VENUES_ROOT.iterdir() if path.is_dir()):
        venue = venue_dir.name
        if selected_venues and venue not in selected_venues:
            continue
        dated_path = venue_dir / f"{compact_date}.json"
        if dated_path.is_file():
            targets.append((venue, dated_path))
    return targets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--venues",
        nargs="*",
        help="Optional venue slugs. Empty processes every dated venue payload.",
    )
    args = parser.parse_args()

    selected = set(args.venues or []) or None
    report: dict[str, Any] = {}
    total_reflected = 0
    try:
        aptitude = CourseAptitudeEvaluator.from_files(date.fromisoformat(args.date))
    except Exception as exc:
        # Course data is display-only.  A broken cache must never stop the
        # existing morning prediction publication path.
        aptitude = CourseAptitudeEvaluator([], {}, class_error=f"course_cache_unavailable:{exc}")

    for venue, dated_path in target_payloads(args.date, selected):
        payload = json.loads(dated_path.read_text(encoding="utf-8"))
        payload_venue = str(
            payload.get("venueId") or payload.get("venue_id") or venue
        )
        if payload.get("date") != args.date or payload_venue != venue:
            report[venue] = {
                "status": "skipped",
                "reason": "payload_identity_mismatch",
            }
            continue

        sources = find_source_files(args.date, venue)
        reflected, expected, warnings = enrich_payload(payload, sources)
        aptitude_counts = aptitude.enrich_payload(payload)
        status = (
            "loaded"
            if expected and reflected == expected
            else "partial"
            if reflected
            else "missing"
        )
        source_status = {
            "status": status,
            "reflected": reflected,
            "expected": expected,
            "source_races": sorted(sources),
            "warnings": warnings,
            "course_6m": aptitude_counts,
        }
        payload.setdefault("sourceStatus", {})["kimarite"] = source_status
        atomic_write_json(dated_path, payload)
        atomic_write_json(dated_path.parent / "latest.json", payload)
        report[venue] = source_status
        total_reflected += reflected

    print(json.dumps(report, ensure_ascii=False, indent=2))

    if not report:
        raise RuntimeError("no_venue_payloads_found")
    if total_reflected == 0:
        raise RuntimeError("kimarite_not_reflected_for_any_venue")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
