from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
WORK_ROOT = REPO_ROOT / "work" / "races"
DATA_ROOT = REPO_ROOT / "data" / "venues" / "heiwajima"


def normalize_name(value: Any) -> str:
    return re.sub(r"[\s\u3000]+", "", str(value or "")).strip()


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_text(path: Path) -> str:
    return path.read_text(
        encoding="utf-8",
        errors="replace",
    )


def percentage(
    block: str,
    labels: tuple[str, ...],
) -> float | None:
    for label in labels:
        match = re.search(
            rf"{re.escape(label)}\s*"
            rf"([0-9]+(?:\.[0-9]+)?)\s*%",
            block,
            flags=re.IGNORECASE,
        )
        if match:
            return float(match.group(1))

    return None


def starts_count(block: str) -> int | None:
    patterns = (
        r"出走回数\s*([0-9]+)\s*回",
        r"出走回数\s*([0-9]+)",
        r"([0-9]+)\s*回\s*$",
    )

    for pattern in patterns:
        matches = list(
            re.finditer(
                pattern,
                block,
                flags=re.MULTILINE,
            )
        )
        if matches:
            return int(matches[-1].group(1))

    return None


def racer_block(
    text: str,
    name: str,
    next_name: str | None,
) -> str:
    compact_name = normalize_name(name)

    if not compact_name:
        return ""

    name_pattern = r"[\s\u3000]*".join(
        map(re.escape, compact_name)
    )

    start_match = re.search(name_pattern, text)

    if not start_match:
        return ""

    end = len(text)

    if next_name:
        compact_next = normalize_name(next_name)

        if compact_next:
            next_pattern = r"[\s\u3000]*".join(
                map(re.escape, compact_next)
            )

            next_match = re.search(
                next_pattern,
                text[start_match.end():],
            )

            if next_match:
                end = (
                    start_match.end()
                    + next_match.start()
                )

    return text[start_match.start():end]


def parse_race_kimarite(
    text: str,
    racers: list[dict],
) -> dict[int, dict]:
    parsed: dict[int, dict] = {}

    for index, racer in enumerate(racers):
        lane = int(racer.get("lane") or 0)

        if lane not in range(1, 7):
            continue

        next_name = (
            racers[index + 1].get("name")
            if index + 1 < len(racers)
            else None
        )

        block = racer_block(
            text,
            racer.get("name") or "",
            next_name,
        )

        if not block:
            continue

        starts = starts_count(block)

        if starts is None:
            continue

        row: dict[str, Any] = {
            "boaters_kimarite_starts": starts,
        }

        if lane == 1:
            values = {
                "boaters_escape_rate": percentage(
                    block,
                    ("逃げ",),
                ),
                "boaters_sashare_rate": percentage(
                    block,
                    ("差され",),
                ),
                "boaters_makurare_rate": percentage(
                    block,
                    ("まくられ",),
                ),
                "boaters_makurare_zashi_rate":
                    percentage(
                        block,
                        (
                            "まくられ差",
                            "まくられ差し",
                        ),
                    ),
            }
        else:
            values = {
                "boaters_nigashi_rate": percentage(
                    block,
                    ("逃し",),
                ),
                "boaters_sashi_rate": percentage(
                    block,
                    ("差し",),
                ),
                "boaters_makuri_rate": percentage(
                    block,
                    ("まくり",),
                ),
                "boaters_makuri_sashi_rate":
                    percentage(
                        block,
                        (
                            "まくり差し",
                            "まくり差",
                        ),
                    ),
            }

        found = False

        for key, value in values.items():
            if value is not None:
                row[key] = value
                found = True

        if found:
            parsed[lane] = row

    return parsed


def race_number_from_path(
    path: Path,
) -> int | None:
    match = re.search(
        r"(?:^|[^0-9])"
        r"([0-9]{1,2})"
        r"(?:R|_data|[^0-9]|$)",
        path.stem,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    race_no = int(match.group(1))

    return (
        race_no
        if 1 <= race_no <= 12
        else None
    )


def find_source_files(
    target_date: str,
) -> dict[int, Path]:
    date_tokens = {
        target_date,
        target_date.replace("-", ""),
    }

    candidates: dict[int, Path] = {}

    for path in WORK_ROOT.glob(
        "**/*_data.txt"
    ):
        path_text = str(path).lower()

        if (
            "heiwajima" not in path_text
            and "平和島" not in str(path)
        ):
            continue

        if not any(
            token in str(path)
            for token in date_tokens
        ):
            continue

        race_no = race_number_from_path(path)

        if race_no is not None:
            candidates[race_no] = path

    return candidates


def enrich_payload(
    payload: dict,
    sources: dict[int, Path],
) -> tuple[int, list[str]]:
    reflected = 0
    warnings: list[str] = []

    for race in payload.get("races") or []:
        race_no = int(
            race.get("race") or 0
        )

        racers = race.get("racers") or []
        source = sources.get(race_no)

        if not source:
            warnings.append(
                f"race_{race_no:02d}:"
                "kimarite_source_missing"
            )
            continue

        parsed = parse_race_kimarite(
            read_text(source),
            racers,
        )

        by_lane = {
            int(racer.get("lane") or 0): racer
            for racer in racers
        }

        for lane, values in parsed.items():
            target = by_lane.get(lane)

            if not target:
                continue

            target.update(values)
            reflected += 1

        if len(parsed) != 6:
            warnings.append(
                f"race_{race_no:02d}:"
                f"kimarite_reflected_"
                f"{len(parsed)}_of_6"
            )

    return reflected, warnings


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--date",
        required=True,
        help="YYYY-MM-DD",
    )

    args = parser.parse_args()

    date_compact = args.date.replace(
        "-",
        "",
    )

    dated_path = (
        DATA_ROOT
        / f"{date_compact}.json"
    )

    latest_path = (
        DATA_ROOT
        / "latest.json"
    )

    if not dated_path.is_file():
        raise FileNotFoundError(
            "heiwajima_payload_missing: "
            f"{dated_path}"
        )

    payload = json.loads(
        dated_path.read_text(
            encoding="utf-8",
        )
    )

    if (
        payload.get("venueId")
        != "heiwajima"
        or payload.get("date")
        != args.date
    ):
        raise RuntimeError(
            "heiwajima_payload_"
            "identity_mismatch"
        )

    sources = find_source_files(args.date)

    reflected, warnings = enrich_payload(
        payload,
        sources,
    )

    payload.setdefault(
        "sourceStatus",
        {},
    )["kimarite"] = {
        "status": (
            "loaded"
            if reflected == 72
            else (
                "partial"
                if reflected
                else "missing"
            )
        ),
        "reflected": reflected,
        "expected": 72,
        "warnings": warnings,
    }

    atomic_write_json(
        dated_path,
        payload,
    )

    atomic_write_json(
        latest_path,
        payload,
    )

    print(
        json.dumps(
            payload["sourceStatus"][
                "kimarite"
            ],
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
