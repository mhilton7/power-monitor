#!/usr/bin/env bash
set -Eeuo pipefail
docker compose build --pull
docker compose run --rm api alembic upgrade head
docker compose up -d --remove-orphans
docker compose ps
