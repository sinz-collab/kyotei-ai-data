from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from omura_engine import OmuraPredictionEngine

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--master-dir", type=Path, default=Path("/mnt/data"))
    p.add_argument("--config", type=Path, default=ROOT / "config" / "engine_config.json")
    args = p.parse_args()
    engine = OmuraPredictionEngine(args.master_dir, args.config)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = engine.predict(payload)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)

if __name__ == "__main__":
    main()
