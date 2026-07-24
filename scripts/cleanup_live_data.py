from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


EXPECTED_ROOT = Path("/opt/sinz-edge/data/live")
DATE_NAME = re.compile(r"\d{4}-\d{2}-\d{2}")
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class Candidate:
    date: str
    path: str
    bytes: int


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def validate_root(root: Path, expected_root: Path = EXPECTED_ROOT) -> Path:
    root_abs = _absolute(root)
    expected_abs = _absolute(expected_root)
    if root_abs != expected_abs:
        raise ValueError(f"cleanup root must be exactly {expected_abs}, got {root_abs}")
    if root_abs.exists() and root_abs.is_symlink():
        raise ValueError(f"cleanup root must not be a symbolic link: {root_abs}")
    return root_abs


def directory_size(path: Path) -> int:
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def find_candidates(root: Path, today: date, retention_days: int) -> list[Candidate]:
    if retention_days < 2:
        raise ValueError("retention_days must be at least 2")
    protected = {today, today - timedelta(days=1)}
    candidates: list[Candidate] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            if not DATE_NAME.fullmatch(entry.name):
                continue
            try:
                entry_date = date.fromisoformat(entry.name)
            except ValueError:
                continue
            if entry_date in protected or (today - entry_date).days <= retention_days:
                continue
            path = Path(entry.path)
            if path.parent != root or path.is_symlink():
                continue
            candidates.append(
                Candidate(date=entry_date.isoformat(), path=str(path), bytes=directory_size(path))
            )
    return sorted(candidates, key=lambda item: item.date)


def emit(event: str, **fields: object) -> None:
    print(
        json.dumps(
            {
                "timestamp": datetime.now(JST).isoformat(timespec="seconds"),
                "event": event,
                **fields,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        flush=True,
    )


def remove_candidates(root: Path, candidates: Iterable[Candidate], dry_run: bool) -> int:
    failures = 0
    for candidate in candidates:
        path = Path(candidate.path)
        try:
            if path.parent != root or path.is_symlink() or not path.is_dir():
                raise ValueError(f"candidate path failed safety validation: {path}")
            if not dry_run:
                shutil.rmtree(path)
            emit(
                "cleanup_skipped_dry_run" if dry_run else "cleanup_deleted",
                **asdict(candidate),
            )
        except Exception as exc:
            failures += 1
            emit("cleanup_delete_failed", path=str(path), error=str(exc))
    return failures


def cleanup(
    root: Path,
    *,
    today: date,
    retention_days: int = 14,
    dry_run: bool = False,
    expected_root: Path = EXPECTED_ROOT,
) -> dict[str, object]:
    root = validate_root(root, expected_root)
    if not root.exists():
        result = {
            "root": str(root),
            "dry_run": dry_run,
            "candidate_count": 0,
            "candidate_bytes": 0,
            "candidate_dates": [],
            "failures": 0,
            "root_missing": True,
        }
        emit("cleanup_root_missing", **result)
        return result
    if not root.is_dir():
        raise ValueError(f"cleanup root is not a directory: {root}")

    candidates = find_candidates(root, today, retention_days)
    summary = {
        "root": str(root),
        "dry_run": dry_run,
        "candidate_count": len(candidates),
        "candidate_bytes": sum(item.bytes for item in candidates),
        "candidate_dates": [item.date for item in candidates],
        "candidates": [asdict(item) for item in candidates],
    }
    emit("cleanup_candidates", **summary)
    failures = remove_candidates(root, candidates, dry_run)
    result = {**summary, "failures": failures, "root_missing": False}
    emit("cleanup_complete", **result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Delete expired SINZ EDGE live JSON.")
    parser.add_argument("--root", type=Path, default=EXPECTED_ROOT)
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = cleanup(
            args.root,
            today=datetime.now(JST).date(),
            retention_days=args.retention_days,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        emit("cleanup_failed", error=str(exc))
        return 2
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
