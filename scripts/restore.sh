#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $# -ne 2 ]]; then echo "usage: $0 /data/backups/power-monitor-TIMESTAMP TARGET_DATABASE" >&2; exit 64; fi
docker compose --profile tools run --rm backup /srv/scripts/restore-container.sh "$1" "$2" --yes
