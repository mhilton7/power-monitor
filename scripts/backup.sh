#!/usr/bin/env bash
set -Eeuo pipefail
docker compose --profile tools run --rm backup /srv/scripts/backup-container.sh
