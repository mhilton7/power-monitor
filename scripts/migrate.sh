#!/usr/bin/env bash
set -Eeuo pipefail
docker compose run --rm api alembic upgrade head
