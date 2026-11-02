from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis" / "heiwajima_fly_v6"
ENGINE = ROOT / "engines" / "heiwajima_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_validated_sources_are_copied_without_changes() -> None:
    assert sha256(ANALYSIS / "model.py") == sha256(ENGINE / "heiwajima_fly_v6_model.py")
    assert sha256(ANALYSIS / "calibrate_stages.py") == sha256(
        ENGINE / "heiwajima_fly_v6_calibration.py"
    )
    assert sha256(ANALYSIS / "fly_v6_best_candidate.json") == sha256(
        ENGINE / "heiwajima_fly_v6_best_candidate.json"
    )


def test_fly_decision_and_ticket_reproduction() -> None:
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "reproduction.json"
        subprocess.run(
            [
                sys.executable,
                str(ANALYSIS / "run_bundle_reproduction.py"),
                "--output",
                str(output),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        report = json.loads(output.read_text(encoding="utf-8"))

    reproduction = report["reproduction"]
    assert reproduction["byte_identical"] is True
    assert reproduction["2026-09_LIVE"]["correct"] == 124
    assert reproduction["2026-09_LIVE"]["races"] == 173
    assert reproduction["2026-10_LIVE"]["correct"] == 51
    assert reproduction["2026-10_LIVE"]["races"] == 77

    tickets = report["ticket_comparison"]
    assert tickets["2026-09 LIVE"]["fly_v6"]["normal12_plus_fly8_hits"] == 69
    assert tickets["2026-09 LIVE"]["fly_v6"]["races"] == 173
    assert tickets["2026-10 LIVE"]["fly_v6"]["normal12_plus_fly8_hits"] == 46
    assert tickets["2026-10 LIVE"]["fly_v6"]["races"] == 77
    combined = report["ticket_comparison_live_combined"]["fly_v6"]
    assert combined["normal12_plus_fly8_hits"] == 115
    assert combined["races"] == 250


def test_saved_fly_tickets_never_use_lane1_as_head() -> None:
    for name in ("replay_202609_live.json", "replay_202610_live.json"):
        report = json.loads((ANALYSIS / name).read_text(encoding="utf-8"))
        for row in report["rows"]:
            assert len(row["normal"]) == 12
            assert len(row["candidate_fly"]) == 8
            assert all(not combo.startswith("1-") for combo in row["candidate_fly"])
