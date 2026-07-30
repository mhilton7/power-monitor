from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BackgroundJob, BackupRun


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


async def bootstrap(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "backup-owner@example.com",
            "display_name": "Backup Owner",
            "password": "Backup-Owner-Password-2026!",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_backup_create_request_is_audited_idempotent_and_path_free(api_client: Any) -> None:
    await bootstrap(api_client)
    payload = {
        "operation": "create",
        "idempotency_key": "backup-create-test-0001",
    }
    created = await api_client.post(
        "/api/v1/backup-requests", headers=csrf(api_client), json=payload
    )
    assert created.status_code == 202, created.text
    assert created.json()["status"] == "queued"
    assert "path" not in created.json()
    repeated = await api_client.post(
        "/api/v1/backup-requests", headers=csrf(api_client), json=payload
    )
    assert repeated.status_code == 202
    assert repeated.json()["id"] == created.json()["id"]
    listed = await api_client.get("/api/v1/backup-requests")
    assert [item["id"] for item in listed.json()] == [created.json()["id"]]
    backups = await api_client.get("/api/v1/backups")
    assert backups.status_code == 200
    assert backups.json()[0]["status"] == "queued"
    assert "path" not in backups.json()[0]


@pytest.mark.asyncio
async def test_backup_create_is_global_single_flight(
    api_client: Any, session: AsyncSession
) -> None:
    await bootstrap(api_client)
    first = await api_client.post(
        "/api/v1/backup-requests",
        headers=csrf(api_client),
        json={"operation": "create", "idempotency_key": "single-flight-first"},
    )
    assert first.status_code == 202
    second = await api_client.post(
        "/api/v1/backup-requests",
        headers=csrf(api_client),
        json={"operation": "create", "idempotency_key": "single-flight-second"},
    )
    assert second.status_code == 409
    assert second.json()["code"] == "backup_operation_active"
    assert int(await session.scalar(select(func.count()).select_from(BackupRun)) or 0) == 1
    assert (
        int(
            await session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "backup_create")
            )
            or 0
        )
        == 1
    )


@pytest.mark.asyncio
async def test_verify_retry_and_delete_lifecycle_are_typed_and_protected(
    api_client: Any, session: AsyncSession
) -> None:
    await bootstrap(api_client)
    now = datetime.now(UTC)
    pending = BackupRun(
        id="11111111-1111-1111-1111-111111111111",
        started_at=now,
        completed_at=now,
        status="verification_failed",
        path="power-monitor-20260730T120000Z-11111111",
        manifest_hash="a" * 64,
        verified_at=None,
        verification_details={},
        encrypted=True,
        verification_attempt_count=1,
        updated_at=now,
    )
    session.add(pending)
    await session.commit()
    verify = await api_client.post(
        f"/api/v1/backups/{pending.id}/verify",
        headers=csrf(api_client),
        json={"idempotency_key": "verify-pending-0001"},
    )
    assert verify.status_code == 202, verify.text
    assert verify.json()["status"] == "verification_queued"
    repeated = await api_client.post(
        f"/api/v1/backups/{pending.id}/verify",
        headers=csrf(api_client),
        json={"idempotency_key": "verify-pending-0001"},
    )
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "verification_queued"

    job = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.job_type == "backup_verify")
    )
    assert job is not None
    job.status = "completed"
    pending.status = "verified"
    pending.verified_at = now
    second = BackupRun(
        id="22222222-2222-2222-2222-222222222222",
        started_at=now,
        completed_at=now,
        status="verified",
        path="power-monitor-20260730T120100Z-22222222",
        manifest_hash="b" * 64,
        verified_at=now,
        verification_details={},
        encrypted=True,
        verification_attempt_count=1,
        updated_at=now,
    )
    session.add(second)
    await session.commit()
    already_verified = await api_client.post(
        f"/api/v1/backups/{second.id}/verify",
        headers=csrf(api_client),
        json={"idempotency_key": "verify-already-complete-0001"},
    )
    assert already_verified.status_code == 202
    assert already_verified.json()["status"] == "verified"
    assert (
        int(
            await session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "backup_verify")
            )
            or 0
        )
        == 1
    )
    removed = await api_client.request(
        "DELETE",
        f"/api/v1/backups/{pending.id}",
        headers=csrf(api_client),
        json={
            "confirmation": "DELETE",
            "backup_id_confirmation": pending.id[:8],
            "reason": "Superseded verified restore point",
        },
    )
    assert removed.status_code == 202, removed.text
    assert removed.json()["status"] == "deleting"

    delete_job = await session.scalar(
        select(BackgroundJob).where(BackgroundJob.job_type == "backup_delete")
    )
    assert delete_job is not None
    delete_job.status = "completed"
    pending.status = "deleted"
    pending.deleted_at = now
    second.status = "verification_failed"
    second.safe_error_code = "RESTORE_FAILED"
    second.safe_error_summary = "The repeat restore verification failed"
    await session.commit()
    protected = await api_client.request(
        "DELETE",
        f"/api/v1/backups/{second.id}",
        headers=csrf(api_client),
        json={
            "confirmation": "DELETE",
            "backup_id_confirmation": second.id[:8],
            "reason": "Attempt to remove final verified restore point",
        },
    )
    assert protected.status_code == 409
    assert protected.json()["code"] == "last_verified_backup_protected"


@pytest.mark.asyncio
async def test_restore_preflight_requires_a_verified_backup(api_client: Any) -> None:
    await bootstrap(api_client)
    missing = await api_client.post(
        "/api/v1/backup-requests",
        headers=csrf(api_client),
        json={
            "operation": "restore_preflight",
            "backup_id": "00000000-0000-0000-0000-000000000000",
            "confirmation": "VERIFY RESTORE",
            "idempotency_key": "restore-preflight-0001",
        },
    )
    assert missing.status_code == 404
    invalid_confirmation = await api_client.post(
        "/api/v1/backup-requests",
        headers=csrf(api_client),
        json={
            "operation": "restore_preflight",
            "backup_id": "00000000-0000-0000-0000-000000000000",
            "confirmation": "restore",
            "idempotency_key": "restore-preflight-0002",
        },
    )
    assert invalid_confirmation.status_code == 422


def test_backup_scheduler_uses_file_backed_password_and_quiet_queue_claims() -> None:
    scheduler = (Path(__file__).resolve().parents[2] / "scripts" / "backup-scheduler.sh").read_text(
        encoding="utf-8"
    )
    assert "source /srv/scripts/container-secrets.sh" in scheduler
    assert "load_file_backed_variable PGPASSWORD" in scheduler
    assert "psql --quiet --tuples-only --no-align" in scheduler
    assert "PGPASSWORD=" not in scheduler
    assert "scheduled-nightly:" in scheduler
    assert "queue_automatic_verification" in scheduler
    assert "last_run_day" not in scheduler
    assert 'find "$backup_root"' not in (
        Path(__file__).resolve().parents[2] / "scripts" / "backup-container.sh"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_replace_all_inventory_confirmation_idempotency_and_single_flight(
    api_client: Any,
    session: AsyncSession,
) -> None:
    await bootstrap(api_client)
    now = datetime.now(UTC)
    existing = [
        BackupRun(
            id="33333333-3333-3333-3333-333333333333",
            started_at=now,
            completed_at=now,
            status="verified",
            path="power-monitor-20260730T120200Z-33333333",
            manifest_hash="c" * 64,
            verified_at=now,
            verification_details={"table_count": 100},
            size_bytes=4000,
            encrypted=True,
            verification_attempt_count=1,
            updated_at=now,
        ),
        BackupRun(
            id="44444444-4444-4444-4444-444444444444",
            started_at=now,
            completed_at=now,
            status="verification_failed",
            path="power-monitor-20260730T120300Z-44444444",
            manifest_hash="d" * 64,
            verified_at=None,
            verification_details={},
            size_bytes=2000,
            encrypted=True,
            verification_attempt_count=1,
            updated_at=now,
        ),
    ]
    session.add_all(existing)
    await session.commit()

    preview = await api_client.get("/api/v1/backups/replace-all-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "existing_backup_count": 2,
        "existing_storage_bytes": 6000,
        "incomplete_backup_count": 0,
        "unverified_backup_count": 1,
        "verified_backup_count": 1,
        "estimated_reclaim_bytes": 6000,
    }
    invalid = await api_client.post(
        "/api/v1/backups/replace-all",
        headers=csrf(api_client),
        json={
            "confirmation": "replace all backups",
            "idempotency_key": "replace-all-invalid-0001",
        },
    )
    assert invalid.status_code == 422

    payload = {
        "confirmation": "REPLACE ALL BACKUPS",
        "idempotency_key": "replace-all-valid-0001",
    }
    queued = await api_client.post(
        "/api/v1/backups/replace-all",
        headers=csrf(api_client),
        json=payload,
    )
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["operation"] == "replace_all"
    assert body["status"] == "queued"
    replacement_id = body["backup_id"]
    assert replacement_id not in body["progress"]["old_backup_ids"]
    assert set(body["progress"]["old_backup_ids"]) == {item.id for item in existing}
    replacement = await session.get(BackupRun, replacement_id)
    assert replacement is not None
    assert replacement.status == "queued"
    assert replacement.trigger_type == "replace_all"

    repeated = await api_client.post(
        "/api/v1/backups/replace-all",
        headers=csrf(api_client),
        json=payload,
    )
    assert repeated.status_code == 202
    assert repeated.json()["id"] == body["id"]
    concurrent = await api_client.post(
        "/api/v1/backups/replace-all",
        headers=csrf(api_client),
        json={
            "confirmation": "REPLACE ALL BACKUPS",
            "idempotency_key": "replace-all-valid-0002",
        },
    )
    assert concurrent.status_code == 409
    assert concurrent.json()["code"] == "backup_operation_active"


@pytest.mark.asyncio
async def test_replace_all_rejects_incomplete_backup_state(
    api_client: Any,
    session: AsyncSession,
) -> None:
    await bootstrap(api_client)
    now = datetime.now(UTC)
    session.add(
        BackupRun(
            id="55555555-5555-5555-5555-555555555555",
            started_at=now,
            completed_at=None,
            status="restoring",
            path="power-monitor-20260730T120400Z-55555555",
            manifest_hash="e" * 64,
            verified_at=now,
            verification_details={},
            size_bytes=3000,
            encrypted=True,
            verification_attempt_count=1,
            updated_at=now,
        )
    )
    await session.commit()

    blocked = await api_client.post(
        "/api/v1/backups/replace-all",
        headers=csrf(api_client),
        json={
            "confirmation": "REPLACE ALL BACKUPS",
            "idempotency_key": "replace-all-blocked-0001",
        },
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "backup_replace_all_incompatible_state"
    assert (
        int(
            await session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(BackgroundJob.job_type == "backup_replace_all")
            )
            or 0
        )
        == 0
    )


def test_replace_all_script_verifies_before_deletion_and_excludes_replacement() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / "scripts" / "replace-all-backups-container.sh").read_text(encoding="utf-8")
    create_at = workflow.index("/srv/scripts/backup-container.sh")
    verify_at = workflow.index("/srv/scripts/verify-backup-container.sh")
    inventory_at = workflow.index("mapfile -t old_backup_ids")
    delete_at = workflow.index("/srv/scripts/delete-backup-container.sh")
    assert create_at < verify_at < inventory_at < delete_at
    assert "value <> :'replacement_id'" in workflow
    assert "status='verified' AND verified_at IS NOT NULL" in workflow
    assert "COALESCE(original_size_bytes, 0) AS original_size_bytes" in workflow
    assert "replacement_backup_id" in workflow
    assert "backup.replace_all_cleanup_incomplete" in workflow
    assert "final_checks_passed" in workflow
    for stage in (
        "preparing",
        "creating_replacement",
        "verifying_checksums",
        "replacement_verified",
        "removing_old_backups",
        "final_checks",
    ):
        assert stage in workflow
    backup_script = (root / "scripts" / "backup-container.sh").read_text(encoding="utf-8")
    verify_script = (root / "scripts" / "verify-backup-container.sh").read_text(encoding="utf-8")
    scheduler_script = (root / "scripts" / "backup-scheduler.sh").read_text(encoding="utf-8")
    assert "writing_database_dump" in backup_script
    assert "writing_supporting_artifacts" in backup_script
    assert "testing_database_restore" in verify_script
    for script in (workflow, backup_script, verify_script, scheduler_script):
        assert "COALESCE(progress, '{}'::jsonb)" not in script
        assert "COALESCE(progress, '{}'::json)::jsonb" in script
    assert "status='backup_failed'" in scheduler_script
    assert "AND status IN ('queued', 'creating')" in scheduler_script
