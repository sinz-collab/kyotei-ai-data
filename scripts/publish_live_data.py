from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from live_common import ROOT, load_config, resolve_root


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Optional batched Git backup for live JSON")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    config = load_config()
    live_root = resolve_root(config, "live_output_root")
    relative = live_root.relative_to(ROOT)
    run(["git", "add", "-f", str(relative)])
    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print("No live data changes.")
        return 0
    stamp = time.strftime("%Y-%m-%d %H:%M")
    commit = run(["git", "commit", "-m", f"Update live race data {stamp}"])
    if commit.returncode:
        print(commit.stderr)
        return commit.returncode
    if args.push:
        pushed = run(["git", "push", "origin", "HEAD:main"])
        if pushed.returncode:
            print(pushed.stderr)
            return pushed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
