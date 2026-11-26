#!/usr/bin/env bash
set -Eeuo pipefail

source "$(dirname "$0")/file_transaction.sh"
root=$(mktemp -d)
trap 'rm -rf -- "$root"' EXIT
app="$root/app"
backup="$root/backup"
mkdir -p "$app/engines/tokoname_v1" "$app/scripts" \
  "$backup/files/engines/tokoname_v1" "$backup/files/scripts" \
  "$backup/new/engines/tokoname_v1" "$backup/new/scripts"
printf old > "$backup/files/engines/tokoname_v1/engine.py"
printf old > "$app/engines/tokoname_v1/engine.py"
touch "$backup/new/scripts/new_worker.py"

# Simulate a partial install followed by a health-check failure.
printf corrupt > "$app/engines/tokoname_v1/engine.py"
printf partial > "$app/scripts/new_worker.py"
restore_engine_files "$app" "$backup" \
  engines/tokoname_v1/engine.py scripts/new_worker.py
[[ $(cat "$app/engines/tokoname_v1/engine.py") == old ]]
[[ ! -e $app/scripts/new_worker.py ]]
echo 'rollback restoration test: ok'
