$ErrorActionPreference = 'Stop'
docker compose run --rm api alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migration failed with exit code $LASTEXITCODE" }
