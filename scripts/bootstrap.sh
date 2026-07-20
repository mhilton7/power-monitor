#!/usr/bin/env bash
set -Eeuo pipefail
test -f .env || { echo 'Copy .env.example to .env and replace every CHANGE_ME value first.' >&2; exit 64; }
if grep -q 'CHANGE_ME' .env; then echo '.env still contains CHANGE_ME values.' >&2; exit 65; fi
docker compose config --quiet
docker compose up -d --build
docker compose ps
