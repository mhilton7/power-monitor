#!/usr/bin/env bash

backup_root_path() {
  realpath /data/backups
}

backup_identifier_from_value() {
  local value=$1 identifier
  if [[ "$value" == /* ]]; then
    local root resolved
    root=$(backup_root_path)
    resolved=$(realpath "$value") || return 1
    case "$resolved" in
      "$root"/power-monitor-*) identifier=${resolved##*/} ;;
      *) return 1 ;;
    esac
  else
    identifier=$value
  fi
  [[ "$identifier" =~ ^power-monitor-[0-9]{8}T[0-9]{6}Z(-[0-9a-f]{8})?$ ]] || return 1
  printf '%s\n' "$identifier"
}

resolve_backup_directory() {
  local identifier root candidate resolved
  identifier=$(backup_identifier_from_value "$1") || return 1
  root=$(backup_root_path)
  candidate="$root/$identifier"
  [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
  resolved=$(realpath "$candidate") || return 1
  [[ "$resolved" == "$root/$identifier" ]] || return 1
  printf '%s\n' "$resolved"
}

resolve_backup_or_trash_directory() {
  local identifier root completed trashed resolved
  identifier=$(backup_identifier_from_value "$1") || return 1
  root=$(backup_root_path)
  completed="$root/$identifier"
  trashed="$root/.trash/$identifier"
  if [[ -d "$completed" && ! -L "$completed" ]]; then
    resolved=$(realpath "$completed") || return 1
    [[ "$resolved" == "$completed" ]] || return 1
  elif [[ -d "$trashed" && ! -L "$trashed" ]]; then
    resolved=$(realpath "$trashed") || return 1
    [[ "$resolved" == "$trashed" ]] || return 1
  else
    return 1
  fi
  printf '%s\n' "$resolved"
}
