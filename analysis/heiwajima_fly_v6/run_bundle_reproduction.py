from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
CALIBRATION = HERE / "calibrate_stages.py"
SAVED_RESULT = HERE / "calibrated_stages_result.json"
CONFIG = HERE / "fly_v6_best_candidate.json"
DATASETS = {
    "2026-09 LIVE": HERE / "replay_202609_live.json",
    "2026-10 LIVE": HERE / "replay_202610_live.json",
    "2026-09 morning": HERE / "replay_202609_morning.json",
    "2026-10 morning": HERE / "replay_202610_morning.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ticket_summary(report):
    return {
        name: {
            "races": values["races"],
            "normal_hits": values["normal_hits"],
            "normal_hit_rate": values["normal_hit_rate"],
            "fly8_hits": values["fly_ticket_hits"],
            "fly8_hit_rate": values["fly_ticket_hit_rate"],
            "normal12_plus_fly8_hits": values["union_hits"],
            "normal12_plus_fly8_hit_rate": values["union_hit_rate"],
        }
        for name, values in (("v2.7", report["baseline"]), ("fly_v6", report["candidate"]))
    }


def combine_ticket_summaries(reports):
    combined = {}
    for name, source in (("v2.7", "baseline"), ("fly_v6", "candidate")):
        races = sum(report[source]["races"] for report in reports)
        normal_hits = sum(report[source]["normal_hits"] for report in reports)
        fly_hits = sum(report[source]["fly_ticket_hits"] for report in reports)
        union_hits = sum(report[source]["union_hits"] for report in reports)
        combined[name] = {
            "races": races,
            "normal_hits": normal_hits,
            "normal_hit_rate": round(normal_hits / races, 8),
            "fly8_hits": fly_hits,
            "fly8_hit_rate": round(fly_hits / races, 8),
            "normal12_plus_fly8_hits": union_hits,
            "normal12_plus_fly8_hit_rate": round(union_hits / races, 8),
        }
    return combined


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(HERE / "bundle_reproduction_report.json"))
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temporary:
        rerun = Path(temporary) / "calibrated_stages_result.json"
        subprocess.run(
            [
                sys.executable,
                str(CALIBRATION),
                "--train",
                str(DATASETS["2026-09 LIVE"]),
                "--holdout",
                str(DATASETS["2026-10 LIVE"]),
                "--morning-train",
                str(DATASETS["2026-09 morning"]),
                "--morning-holdout",
                str(DATASETS["2026-10 morning"]),
                "--output",
                str(rerun),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        saved_sha = sha256(SAVED_RESULT)
        rerun_sha = sha256(rerun)
        rerun_result = load(rerun)

    best = next(stage for stage in rerun_result["stages"] if stage["name"] == "+exhibition_sum")
    config = load(CONFIG)
    if config["features"] != best["coefficients"]:
        raise RuntimeError("best-candidate coefficients differ from the calibrated stage")
    expected = ((best["train"]["correct"], best["train"]["races"]), (best["holdout"]["correct"], best["holdout"]["races"]))
    if expected != ((124, 173), (51, 77)):
        raise RuntimeError(f"unexpected reproduction counts: {expected!r}")
    if saved_sha != rerun_sha:
        raise RuntimeError("calibrated_stages_result.json is not byte reproducible")

    loaded = {name: load(path) for name, path in DATASETS.items()}
    live_reports = [loaded["2026-09 LIVE"], loaded["2026-10 LIVE"]]
    report = {
        "reproduction": {
            "calibrated_result_sha256": saved_sha,
            "byte_identical": True,
            "best_stage": best["name"],
            "2026-09_LIVE": best["train"],
            "2026-10_LIVE": best["holdout"],
        },
        "ticket_comparison": {
            name: ticket_summary(loaded[name]) for name in DATASETS
        },
        "ticket_comparison_live_combined": combine_ticket_summaries(live_reports),
    }
    output = Path(args.output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
