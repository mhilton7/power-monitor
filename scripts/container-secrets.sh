#!/usr/bin/env bash
set -Eeuo pipefail

load_file_backed_variable() {
  local variable_name=$1
  local file_variable_name="${variable_name}_FILE"
  local file_path=${!file_variable_name:-}
  local value

  if [[ -z "$file_path" ]]; then
    return 0
  fi
  [[ -f "$file_path" && -r "$file_path" ]] || {
    printf '%s points to an unreadable secret file\n' "$file_variable_name" >&2
    return 65
  }
  value=$(<"$file_path")
  [[ -n "$value" && "$value" != *$'\n'* && "$value" != *$'\r'* ]] || {
    printf '%s must contain one non-empty text line\n' "$file_variable_name" >&2
    return 65
  }
  printf -v "$variable_name" '%s' "$value"
  export "$variable_name"
}

prepare_backup_key() {
  BACKUP_KEY_PATH=""
  BACKUP_KEY_TEMPORARY=false
  if [[ -n "${BACKUP_ENCRYPTION_KEY_FILE:-}" ]]; then
    [[ -f "$BACKUP_ENCRYPTION_KEY_FILE" && -r "$BACKUP_ENCRYPTION_KEY_FILE" ]] || {
      printf 'BACKUP_ENCRYPTION_KEY_FILE points to an unreadable secret file\n' >&2
      return 65
    }
    [[ -s "$BACKUP_ENCRYPTION_KEY_FILE" ]] || {
      printf 'BACKUP_ENCRYPTION_KEY_FILE is empty\n' >&2
      return 65
    }
    BACKUP_KEY_PATH=$BACKUP_ENCRYPTION_KEY_FILE
  elif [[ -n "${BACKUP_ENCRYPTION_KEY:-}" ]]; then
    BACKUP_KEY_PATH=$(mktemp /tmp/power-monitor-backup-key.XXXXXX)
    chmod 0600 "$BACKUP_KEY_PATH"
    printf '%s' "$BACKUP_ENCRYPTION_KEY" >"$BACKUP_KEY_PATH"
    BACKUP_KEY_TEMPORARY=true
  fi
  export BACKUP_KEY_PATH BACKUP_KEY_TEMPORARY
}

remove_temporary_backup_key() {
  if [[ ${BACKUP_KEY_TEMPORARY:-false} == true && -n "${BACKUP_KEY_PATH:-}" ]]; then
    rm -f -- "$BACKUP_KEY_PATH"
  fi
}

encrypt_backup_artifact() {
  local path=$1
  openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 -md sha256 \
    -pass "file:${BACKUP_KEY_PATH}" -in "$path" -out "${path}.enc"
  rm -f -- "$path"
}

decrypt_backup_artifact() {
  local source=$1
  local destination=$2
  if [[ "$source" == *.enc ]]; then
    [[ -n "${BACKUP_KEY_PATH:-}" ]] || {
      printf 'backup is encrypted; BACKUP_ENCRYPTION_KEY_FILE is required\n' >&2
      return 65
    }
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 -md sha256 \
      -pass "file:${BACKUP_KEY_PATH}" -in "$source" -out "$destination"
  else
    cp -- "$source" "$destination"
  fi
}
