#!/usr/bin/env bash

# Restore allowlisted files from a release backup and verify every result.
# The caller has already validated the manifest, but this helper independently
# rejects traversal before copying or removing anything.
restore_engine_files() {
  [[ $# -ge 3 ]] || return 64
  local app=$1
  local backup=$2
  shift 2
  local relative target backup_hash restored_hash

  [[ -d $app && -d $backup/files && -d $backup/new ]] || return 65
  for relative in "$@"; do
    [[ $relative =~ ^(engines|automation|scripts)/[a-zA-Z0-9_./-]+$ && $relative != *'..'* ]] || return 65
    target=$(realpath -m -- "$app/$relative")
    [[ $target == "$app"/* && ! -L $target ]] || return 65
    if [[ -f $backup/files/$relative ]]; then
      cp -a -- "$backup/files/$relative" "$target" || return 74
    elif [[ -f $backup/new/$relative ]]; then
      rm -f -- "$target" || return 74
    else
      return 65
    fi
  done

  for relative in "$@"; do
    target=$(realpath -m -- "$app/$relative")
    if [[ -f $backup/files/$relative ]]; then
      [[ -f $target ]] || return 74
      backup_hash=$(sha256sum -- "$backup/files/$relative" | cut -d' ' -f1)
      restored_hash=$(sha256sum -- "$target" | cut -d' ' -f1)
      [[ $backup_hash == "$restored_hash" ]] || return 74
    else
      [[ ! -e $target ]] || return 74
    fi
  done
}
