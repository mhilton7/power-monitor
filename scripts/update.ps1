$ErrorActionPreference = 'Stop'
docker compose build --pull
if ($LASTEXITCODE -ne 0) { throw 'Image build failed.' }
docker compose run --rm api alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Migration failed.' }
docker compose up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { throw 'Update startup failed.' }
docker compose ps
