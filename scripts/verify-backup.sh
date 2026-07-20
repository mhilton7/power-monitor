#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -ne 1 ]]; then echo "usage: $0 /data/backups/power-monitor-TIMESTAMP" >&2; exit 64; fi
docker compose --profile tools run --rm backup /srv/scripts/verify-backup-container.sh "$1"
