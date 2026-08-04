from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer
from worker.app.tasks import reconcile_missing_normalized_intervals


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
            assignment_overlap_trigger = await connection.fetchval(
                """
                SELECT pg_get_triggerdef(trigger.oid)
                FROM pg_trigger AS trigger
                WHERE trigger.tgrelid = 'rate_assignments'::regclass
                  AND trigger.tgname = 'trg_rate_assignment_no_overlap'
                  AND NOT trigger.tgisinternal
                """
            )
            migrated = await connection.fetchrow(
                """
                SELECT lifecycle_status, lifecycle_generation, decommission_reason
                FROM devices WHERE id = 'device-migration'
                """
            )
            network_modes = await connection.fetch(
                """
                SELECT direction, mode
                FROM sensor_network_policies
                WHERE site_id = 'site-migration'
                ORDER BY direction
                """
            )
            event_sequence_constraint = await connection.fetchval(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'device_events'::regclass
                  AND conname = 'uq_device_event_sequence'
                """
            )
            event_cursor_table = await connection.fetchval(
                "SELECT to_regclass('public.device_event_sync_cursors')::text"
            )
            ota_active_index = await connection.fetchval(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'firmware_deployments'
                  AND indexname = 'uq_firmware_deployment_active_device'
                """
            )
            ota_release_trust_mode = await connection.fetchval(
                """
                SELECT pg_get_constraintdef(oid)
                FROM pg_constraint
                WHERE conrelid = 'firmware_releases'::regclass
                  AND conname LIKE '%firmware_release_trust_mode'
                """
            )
            history_indexes = {
                row["indexname"]: row["indexdef"]
                for row in await connection.fetch(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname IN (
                        'ix_raw_device_time_end',
                        'ix_normalized_device_time_end',
                        'ix_tier_segment_account_time_recalc',
                        'ix_tier_segment_version_time'
                      )
                    """
                )
            }
            assert revision == "20260803_0030"
            assert table_count == 102
            assert set(history_indexes) == {
                "ix_raw_device_time_end",
                "ix_normalized_device_time_end",
                "ix_tier_segment_account_time_recalc",
                "ix_tier_segment_version_time",
            }
            assert ota_active_index is not None
            assert "WHERE" in ota_active_index
            assert "state" in ota_active_index
            assert "completed" in ota_active_index
            assert ota_release_trust_mode is not None
            assert "existing_device_hmac" in ota_release_trust_mode
            assert event_sequence_constraint == "UNIQUE (device_id, event_sequence)"
            assert event_cursor_table == "device_event_sync_cursors"
            engine = create_async_engine(url)
            try:
                factory = async_sessionmaker(engine, expire_on_commit=False)
                async with factory() as worker_session:
                    assert await reconcile_missing_normalized_intervals(worker_session) == {
                        "queued": 0,
                        "completed": 0,
                        "failed": 0,
                    }
            finally:
                await engine.dispose()
            assert assignment_overlap_trigger is not None
            assert "BEFORE INSERT OR UPDATE" in assignment_overlap_trigger
            assert "prevent_rate_assignment_overlap" in assignment_overlap_trigger
            assert (
                await connection.fetchval(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'utility_bill_extraction_revisions'
                      AND column_name = 'normalized_artifact'
                    """
                )
                == "json"
            )
            confidence_constraint = await connection.fetchval(
                """
                SELECT string_agg(pg_get_constraintdef(oid), ' ')
                FROM pg_constraint
                WHERE conrelid = 'utility_bill_extracted_fields'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%confidence%'
                """
            )
            assert "manual_confirmed" in confidence_constraint
            assert "administrator_confirmed" not in confidence_constraint
            assert dict(migrated) == {
                "lifecycle_status": "decommissioned",
                "lifecycle_generation": 1,
                "decommission_reason": "legacy_revoke",
            }
            assert [dict(row) for row in network_modes] == [
                {"direction": "device_ingress", "mode": "legacy_authenticated_any"},
                {"direction": "server_pull", "mode": "deny_all"},
            ]

            await migrate("downgrade", "20260720_0002")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260720_0002"
            )
            assert await connection.fetchval("SELECT to_regclass('public.rate_sources')") is None
            await migrate("upgrade", "head")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260803_0030"
            )

            await connection.execute("DROP SCHEMA public CASCADE")
            await connection.execute("CREATE SCHEMA public")
            await migrate("upgrade", "20260720_0004")
            await connection.execute(
                "INSERT INTO roles (name, description) VALUES ('admin', 'Existing admin')"
            )
            await connection.execute(
                """
                INSERT INTO users
                    (id, email, display_name, password_hash, is_active,
                     password_changed_at, created_at, updated_at)
                VALUES
                    ('user-prior-schema', 'prior@example.test', 'Prior User',
                     'not-a-real-login-hash', true, $1, $1, $1)
                """,
                now,
            )
            await connection.execute(
                "INSERT INTO user_roles (user_id, role_name) VALUES ('user-prior-schema', 'admin')"
            )
            await migrate("upgrade", "head")
            prior_user = await connection.fetchrow(
                "SELECT all_sites, access_revision, lifecycle_state, is_protected "
                "FROM users WHERE id = 'user-prior-schema'"
            )
            assert dict(prior_user) == {
                "all_sites": True,
                "access_revision": 1,
                "lifecycle_state": "active",
                "is_protected": True,
            }
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM role_permissions WHERE role_name = 'admin'"
                )
                == 69
            )
            status_state = await connection.fetchrow(
                """
                SELECT state.current_revision, revision.registry_version,
                       revision.configuration->>'schema_version' AS schema_version
                FROM status_layout_state AS state
                JOIN status_layout_revisions AS revision
                  ON revision.id = state.current_revision_id
                WHERE state.id = 'current'
                """
            )
            assert dict(status_state) == {
                "current_revision": 3,
                "registry_version": "status-indicators/1.0",
                "schema_version": "power-monitor-status-layout/1.0",
            }

            await connection.execute("DROP SCHEMA public CASCADE")
            await connection.execute("CREATE SCHEMA public")
            await migrate("upgrade", "head")
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == (
                "20260803_0030"
            )
            assert (
                await connection.fetchval(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"
                )
                == 102
            )
        finally:
            await connection.close()
