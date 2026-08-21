from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
ENGINE_DIR = REPO / "engines" / "shimonoseki_v6"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engines.shimonoseki_v6.shimonoseki_engine_v6 import ShimonosekiSiteEngineV6, ENGINE_ID, ENGINE_VERSION


def atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_dynamic_motor_master(date: str, source_root: Path, master_dir: Path, work_dir: Path) -> dict[str, dict]:
    """Build same-day motor score from morning raw motor text when available.

    Failure is non-fatal: engine falls back to the bundled recent10 master.
    """
    raw_dir = source_root / "下関" / date.replace("-", "") / "races"
    if not raw_dir.is_dir() or not list(raw_dir.glob("race_*_motor.txt")):
        return {}
    module_path = ENGINE_DIR / "build_shimonoseki_motor_recent10_master_v1.py"
    spec = importlib.util.spec_from_file_location("shimo_motor_builder", module_path)
    if spec is None or spec.loader is None:
        return {}
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    work_dir.mkdir(parents=True, exist_ok=True)
    history = work_dir / "motor_history.csv"
    master = work_dir / "motor_master.csv"
    try:
        snapshot = mod.collect_snapshot(raw_dir, date)
        rows = mod.build_master(master_dir / "shimonoseki_motor_type_master_v1.csv", snapshot, master)
    except Exception as exc:
        print(json.dumps({"event":"shimonoseki_motor_dynamic_fallback","error":f"{type(exc).__name__}:{exc}"}, ensure_ascii=False), file=sys.stderr)
        return {}
    out = {}
    for row in rows:
        key = str(row.get("motor_no") or "").lstrip("0") or "0"
        out[key] = row
    return out


def resolve_live_race_dir(live_root: Path, date: str, race_no: int) -> Path:
    # Accepted forms:
    # /.../live/YYYY-MM-DD/shimonoseki/NN
    # /.../live (root)
    # exact race directory
    if (live_root / "direct.json").is_file():
        return live_root
    candidate = live_root / date / "shimonoseki" / f"{race_no:02d}"
    if candidate.is_dir():
        return candidate
    candidate2 = live_root / "shimonoseki" / f"{race_no:02d}"
    if candidate2.is_dir():
        return candidate2
    return candidate


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--stage", choices=("preliminary","final"), required=True)
    ap.add_argument("--race", type=int)
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--master-dir", default=str(ENGINE_DIR / "master"))
    ap.add_argument("--source-root", default="work/races")
    ap.add_argument("--live-root", default="/opt/sinz-edge/data/live")
    ap.add_argument("--no-latest", action="store_true")
    args = ap.parse_args()

    data_root = Path(args.data_root)
    dated = data_root / "venues" / "shimonoseki" / f"{args.date.replace('-', '')}.json"
    if not dated.is_file():
        raise FileNotFoundError(dated)
    payload = load_json(dated)
    if payload.get("venueId") != "shimonoseki" or payload.get("date") != args.date:
        raise RuntimeError("Shimonoseki payload identity mismatch")

    engine = ShimonosekiSiteEngineV6(args.master_dir)

    if args.stage == "preliminary":
        dynamic_motor = build_dynamic_motor_master(
            args.date,
            Path(args.source_root),
            Path(args.master_dir),
            data_root / "runtime" / "shimonoseki" / args.date.replace("-", ""),
        )
        payload = engine.apply_preliminary_daily(payload, dynamic_motor or None)
    else:
        if not args.race or not 1 <= args.race <= 12:
            raise ValueError("--race 1..12 is required for final stage")
        race_dir = resolve_live_race_dir(Path(args.live_root), args.date, args.race)
        required = {name: race_dir / f"{name}.json" for name in ("direct","exhibition","original_exhibition")}
        missing = [str(path) for path in required.values() if not path.is_file()]
        if missing:
            raise FileNotFoundError("missing live JSON: " + ", ".join(missing))
        docs = {name: load_json(path) for name, path in required.items()}
        for name, doc in docs.items():
            if doc.get("complete") is not True or doc.get("status") != "complete":
                raise RuntimeError(f"{name}.json is not complete")
        payload = engine.apply_final_race(payload, args.race, docs["direct"], docs["exhibition"], docs["original_exhibition"])

    ok, reason = engine.validate_payload(payload, require_all=True)
    if not ok:
        raise RuntimeError(f"Shimonoseki v6 payload invalid: {reason}")

    atomic_write(dated, payload)
    if not args.no_latest:
        atomic_write(data_root / "venues" / "shimonoseki" / "latest.json", payload)

    print(json.dumps({
        "venue":"shimonoseki",
        "date":args.date,
        "stage":args.stage,
        "race":args.race,
        "engine":ENGINE_ID,
        "engineVersion":ENGINE_VERSION,
        "status":"complete",
        "written":str(dated),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
