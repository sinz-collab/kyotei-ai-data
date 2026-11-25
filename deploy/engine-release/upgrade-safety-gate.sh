#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo 'sudo required' >&2; exit 64; }
app=/usr/local/sbin/sinz-engine-apply
source=deploy/engine-release/sinz-engine-apply
helper=deploy/engine-release/safe_release_gate.py
target=/usr/local/libexec/sinz-engine-release/safe_release_gate.py
[[ -f $app && -f $source && -f $helper ]] || { echo 'gateway bootstrap not present' >&2; exit 75; }
bash -n "$source"
python3 -m py_compile "$helper"
PYTHONPATH=deploy/engine-release python3 -m unittest \
  discover -s deploy/engine-release -p test_safe_release_gate.py -q

backup=/var/backups/sinz-engine-release/$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$backup"
cp -a -- "$app" "$backup/sinz-engine-apply"
install -d -o root -g root -m 0755 "$(dirname "$target")"
install -o root -g root -m 0644 "$helper" "$target"
install -o root -g root -m 0755 "$source" "$app"
echo "Safety gate installed; LIVE service untouched. Backup: $backup"
