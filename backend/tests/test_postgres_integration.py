from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 on a host with Docker",
)
@pytest.mark.asyncio
async def test_postgres_17_migrates_previous_schema_and_clean_database() -> None:
    backend = Path(__file__).resolve().parents[1]
    with PostgresContainer("postgres:17.5-alpine") as postgres:
        url = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql+asyncpg://"
        )
        environment = {**os.environ, "DATABASE_URL": url}

        async def migrate(*arguments: str) -> None:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                *arguments,
                cwd=backend,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            assert process.returncode == 0, (stdout + stderr).decode()

        await migrate("upgrade", "20260720_0001")
        connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            now = datetime.now(UTC)
            await connection.execute(
                """
                INSERT INTO sites
                    (id, name, timezone, allowed_cidrs, allowed_domains,
                     allow_public_polling, created_at, updated_at)
                VALUES
                    ('site-migration', 'Migration site', 'America/Los_Angeles',
                     '[]', '[]', false, $1, $1)
                """,
                now,
            )
            await connection.execute(
                """
                INSERT INTO devices
                    (id, site_id, hardware_id, name, connection_mode,
                     measurement_role, cost_scope, include_in_default_site_total,
                     ct_rating_amps, protocol_version, status, revoked_at,
                     desired_config_version, effective_config_version,
                     created_at, updated_at)
                VALUES
                    ('device-migration', 'site-migration', 'hardware-migration',
                     'Legacy revoked sensor', 'push', 'branch', 'energy_only',
                     false, 100, 'pm-protocol/1.0.0', 'revoked', $1, 1, 0, $1, $1)
                """,
                now,
            )
            await migrate("upgrade", "head")
            revision = await connection.fetchval("SELECT version_num FROM alembic_version")
            table_count = await connection.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
            )
            migrated = await connection.fetchrow(
                """
                SELECT lifecycle_status, lifecycle_generation, decommission_reason
                FROM devices WHERE id = 'device-migration'
                """
            )
            assert revision == "20260720_0004"
            assert table_count == 67
            assert dict(migrated) == {
                "lifecycle_status": "decommissioned",
                "lifecycle_generation": 1,
                "decommission_reason": "legacy_revoke",
            }

            await migrate("downgrade", "20260720_0002")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260720_0002"
            )
            assert await connection.fetchval("SELECT to_regclass('public.rate_sources')") is None
            await migrate("upgrade", "head")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260720_0004"
            )

            await connection.execute("DROP SCHEMA public CASCADE")
            await connection.execute("CREATE SCHEMA public")
            await migrate("upgrade", "head")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260720_0004"
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                )
                == 67
            )
        finally:
            await connection.close()
