#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo 'sudo required' >&2; exit 64; }
app=/usr/local/sbin/sinz-engine-apply
source=deploy/engine-release/sinz-engine-apply
helper=deploy/engine-release/safe_release_gate.py
target=/usr/local/libexec/sinz-engine-release/safe_release_gate.py
transaction_source=deploy/engine-release/file_transaction.sh
transaction_target=/usr/local/libexec/sinz-engine-release/file_transaction.sh
manifest_source=deploy/engine-release/manifests/tokoname.txt
manifest_target=/etc/sinz-engine-release/manifests/tokoname.txt
state=/etc/sinz-engine-release/state/tokoname.tsv
[[ -f $app && -f $source && -f $helper && -f $transaction_source && -f $manifest_source && -f $manifest_target && -f $state ]] || {
  echo 'gateway bootstrap not present' >&2; exit 75;
}
bash -n "$source"
bash -n "$transaction_source"
bash deploy/engine-release/test_file_transaction.sh
python3 -m py_compile "$helper"
PYTHONPATH=deploy/engine-release python3 -m unittest \
  discover -s deploy/engine-release -p test_safe_release_gate.py -q

backup=/var/backups/sinz-engine-release/$(date -u +%Y%m%dT%H%M%SZ)
install -d -o root -g root -m 0700 "$backup"
cp -a -- "$app" "$backup/sinz-engine-apply"
[[ ! -f $transaction_target ]] || cp -a -- "$transaction_target" "$backup/file_transaction.sh"
cp -a -- "$manifest_target" "$backup/tokoname.txt"
cp -a -- "$state" "$backup/tokoname.tsv"
# Preserve the old checksum baseline and fail if an already-managed file was
# modified. Add newly allowlisted files using their exact current state.
declare -A prior=()
while IFS=$'\t' read -r hash relative extra; do
  [[ -n $hash && -n $relative && -z ${extra:-} && -z ${prior[$relative]+x} ]] || exit 65
  prior[$relative]=$hash
done < "$state"
state_tmp=$(mktemp /etc/sinz-engine-release/state/.tokoname.XXXXXXXX)
while IFS= read -r relative; do
  [[ $relative =~ ^(engines|automation|scripts)/[a-zA-Z0-9_./-]+$ && $relative != *'..'* ]] || exit 65
  target_file=$(realpath -m -- "/opt/sinz-edge/$relative")
  [[ $target_file == /opt/sinz-edge/* && ! -L /opt/sinz-edge/$relative ]] || exit 65
  current=-
  [[ ! -f $target_file ]] || current=$(sha256sum -- "$target_file" | cut -d' ' -f1)
  if [[ -n ${prior[$relative]+x} && ${prior[$relative]} != "$current" ]]; then
    rm -f -- "$state_tmp"
    echo "managed file changed before upgrade: $relative" >&2
    exit 75
  fi
  printf '%s\t%s\n' "$current" "$relative" >> "$state_tmp"
done < "$manifest_source"
install -d -o root -g root -m 0755 "$(dirname "$target")"
install -o root -g root -m 0644 "$helper" "$target"
install -o root -g root -m 0644 "$transaction_source" "$transaction_target"
install -o root -g root -m 0755 "$source" "$app"
install -o root -g root -m 0644 "$manifest_source" "$manifest_target"
install -o root -g root -m 0600 "$state_tmp" "$state"
rm -f -- "$state_tmp"
echo "Safety gate installed; LIVE service untouched. Backup: $backup"
