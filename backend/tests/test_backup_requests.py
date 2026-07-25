from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest


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
