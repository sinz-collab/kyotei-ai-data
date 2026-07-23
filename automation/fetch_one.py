from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "venues.json"


def today_jst() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d")


def load_venue(slug: str) -> dict:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    for venue in config["venues"]:
        if venue["slug"] == slug:
            return venue
    raise ValueError(f"unknown venue: {slug}")


def request_text(url: str, timeout: int = 30, attempts: int = 3) -> tuple[str, str]:
    errors = []
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.geturl(), response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"attempt={attempt}:{type(exc).__name__}:{exc}")
            print(f"[http retry] url={url} {errors[-1]}", flush=True)
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise RuntimeError("; ".join(errors))


def venue_is_open(slug: str, date: str) -> dict:
    url = f"https://boaters-boatrace.com/race/{slug}/{date}/1R/data"
    try:
        final_url, html = request_text(url)
    except Exception as exc:
        return {"open": False, "reason": f"precheck_failed:{type(exc).__name__}", "url": url}
    expected = f"/race/{slug}/{date}/1R/"
    body_markers = ("出走表", "レーサー", "全国勝率")
    is_open = expected in final_url and any(marker in html for marker in body_markers)
    return {
        "open": is_open,
        "reason": "race_page_found" if is_open else "not_scheduled",
        "url": url,
        "final_url": final_url,
    }


def split_values(value: object) -> list[str]:
    return [part for part in re.split(r"\s+", str(value).strip()) if part]


def fetch_tide(venue: dict, date: str, output_dir: Path) -> dict:
    url = venue.get("tide_url", "")
    if not url:
        return {"status": "not_configured"}
    try:
        _, html = request_text(url)
        tables = pd.read_html(StringIO(html))
        if len(tables) < 2:
            raise ValueError("tide table not found")
        table = tables[1]
        times = sum((split_values(table.iloc[0, col]) for col in range(1, min(5, table.shape[1]))), [])
        types = sum((split_values(table.iloc[1, col]) for col in range(1, min(5, table.shape[1]))), [])
        levels = sum((split_values(table.iloc[2, col]) for col in range(1, min(5, table.shape[1]))), [])
        events = []
        for kind, time_text, level_text in zip(types, times, levels):
            match = re.search(r"-?\d+", level_text)
            if re.fullmatch(r"\d{1,2}:\d{2}", time_text) and match:
                events.append({"type": kind, "time": time_text, "level": int(match.group(0))})
        events.sort(key=lambda item: tuple(map(int, item["time"].split(":"))))
        if not events:
            raise ValueError("tide events not found")
        payload = {
            "status": "ok",
            "title": f"{venue['name']} タイドグラフ",
            "date": date,
            "source": "潮見表",
            "sourceUrl": url,
            "events": events,
            "summary": " / ".join(
                f"{item['time']} {item['type']} {item['level']}cm" for item in events
            ),
            "raceNotes": {},
        }
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "tide_today.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"status": "ok", "events": len(events)}
    except Exception as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}:{exc}", "url": url}


def count_entries(output_dir: Path) -> int:
    race_dir = output_dir / "races"
    count = 0
    for race_no in range(1, 13):
        path = race_dir / f"race_{race_no:02d}_entry.txt"
        if path.exists() and path.stat().st_size > 500:
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue", required=True)
    parser.add_argument("--date", default=today_jst())
    parser.add_argument("--root", default="work/races")
    args = parser.parse_args()

    venue = load_venue(args.venue)
    root = Path(args.root).resolve()
    output_dir = root / venue["name"] / args.date.replace("-", "")
    status = {
        "slug": venue["slug"],
        "name": venue["name"],
        "date": args.date,
        "open": False,
        "entryCount": 0,
        "fetchAttempts": [],
    }

    precheck = venue_is_open(venue["slug"], args.date)
    status["precheck"] = precheck
    if not precheck["open"]:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "fetch_status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(status, ensure_ascii=False))
        return 0

    command = [
        sys.executable,
        str(HERE / "boaters_fetch.py"),
        "--stadium",
        venue["slug"],
        "--date",
        args.date,
        "--root",
        str(root),
        "--wait",
        "4",
        "--click-wait",
        "1.2",
    ]
    entry_count = 0
    return_code = 1
    for attempt in range(1, 3):
        print(f"[fetch start] venue={venue['slug']} date={args.date} attempt={attempt}", flush=True)
        try:
            process = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=900,
            )
            return_code = process.returncode
            error = ""
        except subprocess.TimeoutExpired:
            return_code = 124
            error = "timeout_after_900_seconds"
        except Exception as exc:
            return_code = 1
            error = f"{type(exc).__name__}:{exc}"
        entry_count = count_entries(output_dir)
        attempt_result = {
            "attempt": attempt,
            "returnCode": return_code,
            "entryCount": entry_count,
            "error": error,
        }
        status["fetchAttempts"].append(attempt_result)
        print(f"[fetch result] {json.dumps(attempt_result, ensure_ascii=False)}", flush=True)
        if return_code == 0 and entry_count == 12:
            break
        if attempt < 2:
            time.sleep(20)

    status.update(
        {
            "open": return_code == 0 and entry_count == 12,
            "entryCount": entry_count,
            "fetchReturnCode": return_code,
            "tide": fetch_tide(venue, args.date, output_dir),
        }
    )
    (output_dir / "fetch_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(status, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
