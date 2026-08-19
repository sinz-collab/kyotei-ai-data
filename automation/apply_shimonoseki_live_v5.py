from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--race", required=True, type=int)
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--live-root", required=True)
    ap.add_argument("--master-dir")
    args = ap.parse_args()

    cmd = [
        sys.executable, str(HERE / "run_shimonoseki_v5.py"),
        "--date", args.date,
        "--stage", "final",
        "--race", str(args.race),
        "--data-root", args.data_root,
        "--live-root", args.live_root,
    ]
    if args.master_dir:
        cmd.extend(["--master-dir", args.master_dir])
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
