#!/usr/bin/env python3
"""Validate the two morning-fetch artifacts and merge them for publication."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date
from pathlib import Path


SHARDS = {
    "east": ("ashiya", "shimonoseki", "toda", "wakamatsu"),
    "west": ("tokoname", "omura", "karatsu", "fukuoka", "heiwajima", "biwako", "naruto"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--east", type=Path, required=True)
    parser.add_argument("--west", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name("venues.json"),
    )
    return parser.parse_args()


def validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid target date: {value}") from exc
    if parsed.isoformat() != value:
        raise RuntimeError(f"invalid target date: {value}")
    return value.replace("-", "")


def artifact_root(path: Path) -> Path:
    wrapped = path / "work" / "races"
    if not wrapped.is_dir():
        return path
    outside_wrapper = [
        item
        for item in path.rglob("*")
        if item.is_file() and wrapped not in item.parents
    ]
    if outside_wrapper:
        raise RuntimeError(
            f"unexpected content outside work/races wrapper: {outside_wrapper[0]}"
        )
    return wrapped


def allowed_relative_path(relative: Path, date_dir: str) -> bool:
    parts = relative.parts
    if len(parts) == 3:
        return parts[1] == date_dir and parts[2] in {
            "fetch_status.json",
            "tide_today.json",
        }
    if len(parts) == 4 and parts[1] == date_dir and parts[2] == "races":
        name = parts[3]
        return any(
            name.endswith(suffix)
            for suffix in (
                "_entry.txt",
                "_data.txt",
                "_motor.txt",
                "_boaters_local_st.json",
            )
        )
    return False


def validate_shard(
    shard: str,
    source: Path,
    target_date: str,
    date_dir: str,
    venues: dict[str, str],
) -> list[tuple[Path, Path]]:
    root = artifact_root(source)
    if not root.is_dir():
        raise RuntimeError(f"{shard} artifact directory is missing: {source}")

    expected_slugs = set(SHARDS[shard])
    if not expected_slugs <= venues.keys():
        missing = sorted(expected_slugs - venues.keys())
        raise RuntimeError(f"venues.json is missing configured venues: {missing}")

    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError(f"{shard} artifact is empty")

    copies: list[tuple[Path, Path]] = []
    status_slugs: set[str] = set()
    venue_by_name = {name: slug for slug, name in venues.items()}
    expected_names = {venues[slug] for slug in expected_slugs}

    for path in files:
        if path.is_symlink():
            raise RuntimeError(f"symlinks are not allowed in artifacts: {path}")
        relative = path.relative_to(root)
        if not allowed_relative_path(relative, date_dir):
            raise RuntimeError(f"unexpected path in {shard} artifact: {relative}")
        venue_name = relative.parts[0]
        if venue_name not in expected_names:
            raise RuntimeError(
                f"unexpected venue in {shard} artifact: {venue_name}"
            )
        copies.append((path, relative))

        if relative.name == "fetch_status.json":
            try:
                status = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"invalid fetch status JSON: {relative}") from exc
            slug = venue_by_name[venue_name]
            if (
                status.get("date") != target_date
                or status.get("slug") != slug
                or status.get("name") != venue_name
            ):
                raise RuntimeError(
                    f"fetch status identity mismatch for {slug}: "
                    f"{status.get('slug')}/{status.get('name')}/{status.get('date')}"
                )
            if slug in status_slugs:
                raise RuntimeError(f"duplicate fetch status for {slug}")
            status_slugs.add(slug)

    if status_slugs != expected_slugs:
        raise RuntimeError(
            f"{shard} fetch status mismatch: "
            f"missing={sorted(expected_slugs - status_slugs)} "
            f"unexpected={sorted(status_slugs - expected_slugs)}"
        )
    return copies


def main() -> int:
    args = parse_args()
    date_dir = validate_date(args.date)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    venues = {item["slug"]: item["name"] for item in config.get("venues", [])}
    expected = set(SHARDS["east"]) | set(SHARDS["west"])
    if set(venues) != expected or len(expected) != 11:
        raise RuntimeError(
            "venue configuration must contain exactly the 11 morning venues: "
            f"configured={sorted(venues)}"
        )
    if args.output.exists() and any(args.output.iterdir()):
        raise RuntimeError(f"output directory must be empty: {args.output}")

    copies: list[tuple[Path, Path]] = []
    destinations: set[Path] = set()
    for shard, source in (("east", args.east), ("west", args.west)):
        for path, relative in validate_shard(
            shard, source, args.date, date_dir, venues
        ):
            if relative in destinations:
                raise RuntimeError(f"duplicate artifact path: {relative}")
            destinations.add(relative)
            copies.append((path, relative))

    for source, relative in copies:
        destination = args.output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    print(
        f"Validated and merged {len(copies)} files for "
        f"{len(expected)} venues on {args.date}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
