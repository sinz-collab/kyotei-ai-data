from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from live_common import process_lock  # noqa: E402


RUNTIME_COMPONENTS = (
    "engines/tokoname_v1/engine/predictor.py",
    "engines/tokoname_v1/tokoname_site_pipeline.py",
    "engines/tokoname_v1/fly_tickets_v4.py",
    "engines/tokoname_v1/models/tokoname_model_features_v1_0.pkl",
    "engines/tokoname_v1/models/tokoname_model_win_v1_0.pkl",
    "engines/tokoname_v1/models/tokoname_model_top2_v1_0.pkl",
    "engines/tokoname_v1/models/tokoname_model_top3_v1_0.pkl",
    "engines/tokoname_v1/models/tokoname_fly_tickets_v4/metadata.json",
    "engines/tokoname_v1/models/tokoname_fly_tickets_v4/head.txt",
    "engines/tokoname_v1/models/tokoname_fly_tickets_v4/inside_position.txt",
    "engines/tokoname_v1/models/tokoname_fly_tickets_v4/pair.txt",
)


def self_check() -> dict:
    from engines.tokoname_v1 import fly_tickets_v4, tokoname_site_pipeline
    from engines.tokoname_v1.engine import predictor

    model_dir = ROOT / "engines" / "tokoname_v1" / "models"
    features = predictor.joblib.load(model_dir / predictor.FEATURES_FILE)
    normal_models = {
        name: predictor.joblib.load(model_dir / filename)
        for name, filename in predictor.MODEL_FILES.items()
    }
    fly_metadata, _, fly_models = fly_tickets_v4._load_artifacts(
        str((model_dir / fly_tickets_v4.ARTIFACT_DIRECTORY).resolve())
    )
    if features is None or set(normal_models) != {"win", "top2", "top3"}:
        raise RuntimeError("tokoname_normal_ticket_models_incomplete")
    if fly_metadata.get("modelVersion") != fly_tickets_v4.MODEL_VERSION or len(fly_models) != 3:
        raise RuntimeError("tokoname_fly_ticket_models_incomplete")

    hashes = {}
    for relative in RUNTIME_COMPONENTS:
        path = ROOT / relative
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "status": "ok",
        "process": "tokoname_prediction_worker",
        "engine": tokoname_site_pipeline.ENGINE_NAME,
        "engineVersion": tokoname_site_pipeline.ENGINE_VERSION,
        "normalTicketsSource": "engines/tokoname_v1/engine/predictor.py",
        "normalModelsLoaded": sorted(normal_models),
        "flyTicketsVersion": fly_tickets_v4.MODEL_VERSION,
        "flyTicketsSource": "engines/tokoname_v1/fly_tickets_v4.py",
        "flyModelsLoaded": len(fly_models),
        "componentHashes": hashes,
    }


def parse_races(value: str) -> list[int]:
    races = sorted({int(token.strip()) for token in value.split(",") if token.strip()})
    if not races or not set(races).issubset(set(range(1, 13))):
        raise argparse.ArgumentTypeError("races must be comma-separated values from 1 to 12")
    return races


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated Tokoname prediction worker")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--date")
    parser.add_argument("--morning-root", type=Path)
    parser.add_argument("--live-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument(
        "--lock-path",
        type=Path,
        default=ROOT / "run" / "venues" / "tokoname-engine.lock",
    )
    parser.add_argument("--races", type=parse_races, default=list(range(1, 13)))
    args = parser.parse_args()

    # msvcrt cannot lock a byte beyond EOF. Production Linux uses flock, but
    # seed the portable lock file so Windows validation exercises the same path.
    if os.name == "nt":
        args.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not args.lock_path.exists() or args.lock_path.stat().st_size == 0:
            args.lock_path.write_text("0", encoding="ascii")
    with process_lock(args.lock_path) as acquired:
        if not acquired:
            print(json.dumps({"status": "busy", "reason": "tokoname_engine_lock_busy"}))
            return 75
        if args.self_check:
            print(json.dumps(self_check(), ensure_ascii=False, sort_keys=True))
            return 0
        if not all((args.date, args.morning_root, args.live_root, args.output_root)):
            parser.error("prediction mode requires date and all three roots")

        # Import only after acquiring the distribution lock.  A new process is
        # used for every call, so no module or model cache crosses releases.
        from stage_tokoname_predictions import stage_tokoname_predictions

        report = stage_tokoname_predictions(
            args.date,
            morning_root=args.morning_root,
            live_root=args.live_root,
            output_root=args.output_root,
            race_numbers=args.races,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
