"""Fail-closed race-calendar gate for engine release.

The publisher's venue manifest must be current and complete. Legacy callers can
still require a system-wide quiet window. Venue-scoped callers use only the
requested venue's verified schedule, with the same quiet buffer. Filesystem
locks are handled separately by the privileged deployment wrapper.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def validate_safe_window(
    manifest: dict,
    now: datetime,
    quiet_minutes: int = 45,
    venue: str | None = None,
) -> tuple[bool, str]:
    if now.tzinfo is None or quiet_minutes < 30:
        return False, "invalid_time_or_quiet_period"
    now = now.astimezone(JST)
    try:
        date = datetime.strptime(manifest["date"], "%Y-%m-%d").date()
        updated = datetime.fromisoformat(manifest["updatedAt"])
        if updated.tzinfo is None:
            return False, "manifest_timezone_missing"
        updated = updated.astimezone(JST)
    except (KeyError, ValueError, TypeError):
        return False, "invalid_manifest_date"
    if updated > now + timedelta(minutes=2):
        return False, "manifest_timestamp_in_future"
    if date == now.date():
        max_age = timedelta(hours=4)
    elif date == now.date() - timedelta(days=1) and now.hour < 6:
        max_age = timedelta(hours=12)
    else:
        return False, "manifest_date_not_current"
    if now - updated > max_age:
        return False, "manifest_stale"

    venues = manifest.get("venues")
    if not isinstance(venues, list) or len(venues) < 24:
        return False, "venue_manifest_incomplete"
    if any(not isinstance(v, dict) for v in venues):
        return False, "invalid_venue_list"
    names = [v.get("slug") for v in venues]
    if len(set(names)) != len(names) or any(not v for v in names):
        return False, "invalid_venue_list"
    target = None
    if venue is not None:
        matches = [item for item in venues if item.get("slug") == venue]
        if len(matches) != 1:
            return False, "target_venue_missing"
        target = matches[0]
        if target.get("open") not in (True, False):
            return False, "target_venue_state_unknown"
    active = [v for v in venues if v.get("open") is True]
    if not active and target is None:
        # A missing morning publication must not be mistaken for a rest day.
        return False, "no_verified_open_venue"
    first = None
    last = None
    for active_venue in active:
        if active_venue.get("date") != date.isoformat() or active_venue.get("entryCount") != 12:
            return False, "active_venue_not_verified"
        deadlines = active_venue.get("deadlines")
        if not isinstance(deadlines, list) or len(deadlines) != 12:
            return False, "race_deadlines_incomplete"
        seen = set()
        for item in deadlines:
            if not isinstance(item, dict) or type(item.get("race")) is not int:
                return False, "invalid_race_deadline"
            race = item["race"]
            if not 1 <= race <= 12 or race in seen:
                return False, "invalid_race_numbers"
            seen.add(race)
            try:
                hm = datetime.strptime(item["deadline"], "%H:%M").time()
            except (KeyError, TypeError, ValueError):
                return False, "invalid_race_deadline"
            deadline = datetime.combine(date, hm, tzinfo=JST)
            if target is None or active_venue.get("slug") == target.get("slug"):
                if first is None or deadline < first:
                    first = deadline
                if last is None or deadline > last:
                    last = deadline
    if target is not None and target.get("open") is False:
        if target.get("availabilityReason") != "not_scheduled":
            return False, "target_venue_availability_unverified"
        return True, "target_venue_not_running"
    # Never use yesterday's timetable to infer today's pre-race safety.
    if date == now.date() and first is not None and now <= first - timedelta(minutes=quiet_minutes):
        reason = (
            "target_venue_before_first_deadline_and_quiet"
            if target
            else "all_venues_before_first_deadline_and_quiet"
        )
        return True, reason
    if last is None or now < last + timedelta(minutes=quiet_minutes):
        return False, "races_not_safely_finished"
    reason = (
        "target_venue_finished_and_quiet"
        if target
        else "all_races_finished_and_quiet"
    )
    return True, reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quiet-minutes", type=int, default=45)
    parser.add_argument("--venue")
    args = parser.parse_args()
    try:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        allowed, reason = validate_safe_window(
            data,
            datetime.now(JST),
            args.quiet_minutes,
            args.venue,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        allowed, reason = False, "manifest_unavailable"
    print(f"engine_release_gate={'allow' if allowed else 'deny'} reason={reason}")
    return 0 if allowed else 75


if __name__ == "__main__":
    raise SystemExit(main())
