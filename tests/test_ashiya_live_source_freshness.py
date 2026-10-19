from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))

from apply_ashiya_live_v1 import apply_live_fields  # noqa: E402


def write_snapshot(root, name, fetched_at, data):
    (root / name).write_text(json.dumps({
        "status": "complete",
        "complete": True,
        "fetched_at": fetched_at,
        "data": data,
    }), encoding="utf-8")


def test_older_live_snapshot_cannot_overwrite_newer_input(tmp_path):
    payload = {"races": [{
        "race": 1,
        "racers": [{"lane": lane} for lane in range(1, 7)],
        "live": {
            "exhibition": {"marker": "new"},
            "sourceFetchedAt": {"exhibition": "2026-10-19T09:10:00+09:00"},
        },
    }]}
    write_snapshot(
        tmp_path,
        "exhibition.json",
        "2026-10-19T09:05:00+09:00",
        {"marker": "old"},
    )
    actual = apply_live_fields(payload, 1, tmp_path)
    assert actual["races"][0]["live"]["exhibition"]["marker"] == "new"
    assert actual["races"][0]["live"]["sourceFetchedAt"]["exhibition"] == "2026-10-19T09:10:00+09:00"


def test_newer_live_snapshot_is_applied(tmp_path):
    payload = {"races": [{
        "race": 1,
        "racers": [{"lane": lane} for lane in range(1, 7)],
        "live": {
            "exhibition": {"marker": "old"},
            "sourceFetchedAt": {"exhibition": "2026-10-19T09:05:00+09:00"},
        },
    }]}
    write_snapshot(
        tmp_path,
        "exhibition.json",
        "2026-10-19T09:10:00+09:00",
        {"marker": "new"},
    )
    actual = apply_live_fields(payload, 1, tmp_path)
    assert actual["races"][0]["live"]["exhibition"]["marker"] == "new"
