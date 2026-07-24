from __future__ import annotations

import argparse
import shutil
import subprocess
import time
from pathlib import Path

from live_common import ROOT, load_config, process_lock, resolve_root


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def copy_changed_live_files(source_root: Path, repo_root: Path) -> int:
    destination_root = repo_root / "data" / "live"
    copied = 0
    for source in source_root.rglob("*"):
        if not source.is_file() or source.is_symlink():
            continue
        destination = destination_root / source.relative_to(source_root)
        if destination.exists() and source.read_bytes() == destination.read_bytes():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied += 1
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Optional batched Git backup for live JSON")
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    config = load_config()
    live_root = resolve_root(config, "live_output_root")
    publish_repo = resolve_root(config, "publish_repo_root")
    lock_path = resolve_root(config, "publish_lock_path")
    with process_lock(lock_path) as acquired:
        if not acquired:
            print("Another live publisher is running.")
            return 0
        if not (publish_repo / ".git").is_dir():
            print(f"Publisher repository is not initialized: {publish_repo}")
            return 2
        if args.push:
            pulled = run(["git", "pull", "--rebase", "origin", "main"], publish_repo)
            if pulled.returncode:
                print(pulled.stderr)
                return pulled.returncode
        copied = copy_changed_live_files(live_root, publish_repo)
        run(["git", "add", "-f", "data/live"], publish_repo)
        if run(["git", "diff", "--cached", "--quiet"], publish_repo).returncode == 0:
            print("No live data changes.")
            return 0
        stamp = time.strftime("%Y-%m-%d %H:%M")
        commit = run(
            ["git", "commit", "-m", f"Update live race data {stamp} ({copied} files)"],
            publish_repo,
        )
        if commit.returncode:
            print(commit.stderr)
            return commit.returncode
        if args.push:
            pushed = run(["git", "push", "origin", "HEAD:main"], publish_repo)
            if pushed.returncode:
                pulled = run(["git", "pull", "--rebase", "origin", "main"], publish_repo)
                if pulled.returncode:
                    print(pulled.stderr)
                    return pulled.returncode
                pushed = run(["git", "push", "origin", "HEAD:main"], publish_repo)
                if pushed.returncode:
                    print(pushed.stderr)
                    return pushed.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
