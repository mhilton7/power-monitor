from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession

from app.access import (
    ADMIN_ONLY_PERMISSIONS,
    ALL_PERMISSIONS,
    BUILTIN_ROLE_PERMISSIONS,
    require_data_reset_administrator,
    validate_permissions,
)
from app.data_reset.service import (
    _configuration_transition_state,
    _supports_data_reset,
    redact_history_values,
)
from app.db.models import (
    DataResetOperation,
    DataResetParticipant,
    DataResetPlan,
    DataResetPricingBaseline,
    DeviceCapability,
    DeviceDataState,
    DeviceHeartbeat,
    RawReading,
    SiteDataState,
    SyncCursor,
)
from app.main import app
from app.problem import ProblemError
from app.schemas import (
    DataResetConfirmationPhrases,
    DataResetExecuteRequest,
    DataResetParticipantView,
    DataResetPlanRequest,
    DeviceCapabilities,
    DeviceEventInput,
    HeartbeatDataResetStatus,
    ReadingBatch,
    SensorDataResetCommitRequest,
    SensorDataResetPrepareRequest,
    SensorDataResetResponse,
)
from app.security.protocol import (
    calculate_data_reset_receipt_digest,
    canonical_data_reset_receipt,
    derive_data_reset_receipt_key,
    verify_data_reset_receipt_digest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_configuration_transition_uses_desired_and_signed_effective_revisions() -> None:
    synchronized = _configuration_transition_state(
        desired_version=3,
        effective_version=3,
        pending_versions=(("old", 2), ("reported", 3)),
    )
    assert synchronized == (False, [], ["old", "reported"])

    missing_pending_row = _configuration_transition_state(
        desired_version=3,
        effective_version=2,
        pending_versions=(),
    )
    assert missing_pending_row == (True, [], [])

    newer_pending_row = _configuration_transition_state(
        desired_version=3,
        effective_version=3,
        pending_versions=(("future", 4),),
    )
    assert newer_pending_row == (True, ["future"], [])

    uninitialized_baseline = _configuration_transition_state(
        desired_version=1,
        effective_version=0,
        pending_versions=(),
        uninitialized_baseline=True,
    )
    assert uninitialized_baseline == (False, [], [])


def test_reset_metadata_has_durable_generation_and_boundary_foundation() -> None:
    assert DataResetPlan.__tablename__ == "data_reset_plans"
    assert DataResetOperation.__tablename__ == "data_reset_operations"
    assert DataResetParticipant.__tablename__ == "data_reset_participants"
    assert SiteDataState.__tablename__ == "site_data_states"
    assert DeviceDataState.__tablename__ == "device_data_states"
    assert DataResetPricingBaseline.__tablename__ == "data_reset_pricing_baselines"

    assert {"data_generation", "reset_boundary"} <= set(SyncCursor.__table__.columns.keys())
    assert "data_generation" in DeviceHeartbeat.__table__.columns
    assert "data_generation" in RawReading.__table__.columns
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in RawReading.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    # Generations reject stale payloads, while the product-wide immutable raw
    # identity remains exactly (device_id, sequence). Sequence floors never
    # regress or reuse values across a reset.
    assert ("device_id", "sequence") in unique_columns
    assert ("device_id", "data_generation", "sequence") not in unique_columns

    operation_indexes = {index.name: index for index in DataResetOperation.__table__.indexes}
    active = operation_indexes["uq_data_reset_operation_active_site"]
    assert active.unique is True
    predicate = str(active.dialect_options["postgresql"]["where"])
    assert "completed_with_resets_pending_on_reconnect" not in predicate
    assert "'completed','cancelled','failed_before_commit'" in predicate

    participant_columns = set(DataResetParticipant.__table__.columns.keys())
    assert {
        "reset_generation",
        "reset_boundary",
        "prepare_receipt_safe",
        "commit_receipt_safe",
        "prepare_receipt_digest",
        "commit_receipt_digest",
        "preservation_hash_before",
        "preservation_hash_after",
    } <= participant_columns
    assert (
        not {
            "wifi_password",
            "enrollment_secret",
            "hmac_key",
            "administrator_password",
        }
        & participant_columns
    )


def test_reset_permission_is_high_risk_builtin_admin_only() -> None:
    assert "system.data_reset" in ALL_PERMISSIONS
    assert "system.data_reset" in ADMIN_ONLY_PERMISSIONS
    assert "system.data_reset" in BUILTIN_ROLE_PERMISSIONS["admin"]
    assert all(
        "system.data_reset" not in permissions
        for role, permissions in BUILTIN_ROLE_PERMISSIONS.items()
        if role != "admin"
    )
    require_data_reset_administrator(
        roles=frozenset({"admin"}), permissions=frozenset({"system.data_reset"})
    )
    with pytest.raises(ProblemError) as non_admin:
        require_data_reset_administrator(
            roles=frozenset({"operator"}), permissions=frozenset({"system.data_reset"})
        )
    assert non_admin.value.code == "data_reset_administrator_required"
    with pytest.raises(ProblemError) as custom_role:
        validate_permissions({"system.data_reset"})
    assert custom_role.value.code == "permission_admin_only"


def test_reset_request_contracts_fail_closed_for_destructive_choices() -> None:
    phrases = DataResetConfirmationPhrases()
    assert phrases.verified_backup == "RESET ALL READINGS AND PRICING HISTORY"
    assert (
        phrases.permanent_without_backup
        == "PERMANENTLY RESET ALL READINGS AND PRICING HISTORY WITHOUT BACKUP"
    )
    plan = DataResetPlanRequest(
        site_id="8ee76f54-5652-4a3f-9f87-8c481fdb5bc2",
        categories=[
            "measurement_history",
            "cost_history",
            "pricing_history",
            "generated_outputs",
        ],
    )
    assert plan.delete_imported_bill_documents is False
    assert plan.disconnected_sensor_policy == "defer_until_reconnect"
    with pytest.raises(ValidationError):
        DataResetPlanRequest(
            site_id=plan.site_id,
            categories=["measurement_history", "measurement_history"],
        )

    with pytest.raises(ValidationError):
        DataResetExecuteRequest(
            plan_id="6e845730-5f7d-49a4-a403-3173e43cdce0",
            plan_revision=1,
            idempotency_key="reset-client-1",
            reason="Permanent commissioning cleanup",
            backup_mode="permanent_without_backup",
            confirmation_phrase="PERMANENTLY RESET Home DATA WITHOUT BACKUP",
            permanent_without_backup_acknowledged=False,
        )


def test_device_reset_and_generation_wire_contracts_are_additive() -> None:
    prepare = SensorDataResetPrepareRequest(
        operation_id="187da6e7-c0da-4f95-a2d2-740b874ed9a4",
        device_id="b09baa6a-273f-4338-88e1-af0b47989036",
        target_generation=2,
        reset_timestamp=datetime.now(UTC),
        plan_revision=1,
        plan_digest="a" * 64,
        categories=["measurement_history"],
        expected_boundary=91,
        server_highest_contiguous=90,
        server_maximum_seen=91,
        expected_firmware_version="1.0.18",
        expected_build_hash=None,
        expected_card_generation=None,
    )
    commit = SensorDataResetCommitRequest(
        operation_id=prepare.operation_id,
        device_id=prepare.device_id,
        target_generation=prepare.target_generation,
        plan_revision=prepare.plan_revision,
        plan_digest=prepare.plan_digest,
        approved_boundary=91,
        prepared_receipt_digest="b" * 64,
    )
    assert prepare.protocol == commit.protocol == "data-reset/1.0.0"

    reading = {
        "sequence": 92,
        "boot_id": "1b79f263-bd3a-4a53-9251-4c4278f5536a",
        "interval_start": "2026-08-06T08:00:00Z",
        "interval_end": "2026-08-06T08:01:00Z",
        "time_trusted": True,
        "energy_method": "pzem-counter-delta",
        "ct_rating_amps": "100",
        "quality_flags": [],
        "firmware_version": "1.0.18",
        "data_generation": 2,
    }
    batch = ReadingBatch(
        protocol_version="pm-protocol/1.0.0",
        device_id=prepare.device_id,
        data_generation=2,
        readings=[reading],
    )
    assert batch.data_generation == batch.readings[0].data_generation == 2
    with pytest.raises(ValidationError):
        ReadingBatch(
            protocol_version="pm-protocol/1.0.0",
            device_id=prepare.device_id,
            data_generation=3,
            readings=[reading],
        )


@pytest.mark.parametrize(
    "failure_code",
    ["", "Sensor_Reset_Failed", "sensor-reset-failed", "_sensor_reset", "a" * 81],
)
def test_reset_failure_codes_are_bounded_safe_tokens(failure_code: str) -> None:
    heartbeat = {
        "state": "attention_required",
        "checkpoint": "attention_required",
        "failure_code": "sensor_reset_failed",
    }
    assert HeartbeatDataResetStatus.model_validate(heartbeat).failure_code == (
        "sensor_reset_failed"
    )
    response = {
        "operation_id": "187da6e7-c0da-4f95-a2d2-740b874ed9a4",
        "device_id": "b09baa6a-273f-4338-88e1-af0b47989036",
        "target_generation": 2,
        **heartbeat,
    }
    assert SensorDataResetResponse.model_validate(response).failure_code == ("sensor_reset_failed")

    with pytest.raises(ValidationError):
        HeartbeatDataResetStatus.model_validate({**heartbeat, "failure_code": failure_code})
    with pytest.raises(ValidationError):
        SensorDataResetResponse.model_validate({**response, "failure_code": failure_code})


def test_reset_boundaries_and_protocol_sequences_fit_signed_storage() -> None:
    maximum_boundary = 2**63 - 3
    prepare_payload = {
        "operation_id": "187da6e7-c0da-4f95-a2d2-740b874ed9a4",
        "device_id": "b09baa6a-273f-4338-88e1-af0b47989036",
        "target_generation": 2,
        "reset_timestamp": datetime.now(UTC),
        "plan_revision": 1,
        "plan_digest": "a" * 64,
        "categories": ["measurement_history"],
        "expected_boundary": maximum_boundary,
        "server_highest_contiguous": maximum_boundary,
        "server_maximum_seen": maximum_boundary,
        "expected_firmware_version": "1.0.18",
        "expected_build_hash": None,
        "expected_card_generation": None,
    }
    assert SensorDataResetPrepareRequest.model_validate(prepare_payload).expected_boundary == (
        maximum_boundary
    )
    for field in (
        "expected_boundary",
        "server_highest_contiguous",
        "server_maximum_seen",
    ):
        with pytest.raises(ValidationError):
            SensorDataResetPrepareRequest.model_validate(
                {**prepare_payload, field: maximum_boundary + 1}
            )
    with pytest.raises(ValidationError):
        SensorDataResetCommitRequest(
            operation_id=prepare_payload["operation_id"],
            device_id=prepare_payload["device_id"],
            target_generation=2,
            plan_revision=1,
            plan_digest="a" * 64,
            approved_boundary=maximum_boundary + 1,
            prepared_receipt_digest="b" * 64,
        )
    with pytest.raises(ValidationError):
        DeviceEventInput(
            event_id="sequence-overflow",
            occurred_at=datetime.now(UTC),
            category="security",
            severity="error",
            evidence={"event_sequence": 2**63},
        )


def test_sensor_failure_code_is_outer_status_not_hmac_receipt_evidence() -> None:
    operation_id = "187da6e7-c0da-4f95-a2d2-740b874ed9a4"
    device_id = "b09baa6a-273f-4338-88e1-af0b47989036"
    unsafe_receipt = json.dumps(
        {
            "protocol": "data-reset/1.0.0",
            "operation_id": operation_id,
            "device_id": device_id,
            "target_generation": 2,
            "failure_code": "sensor_reset_failed",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ValidationError, match="non-redacted field"):
        SensorDataResetResponse(
            operation_id=operation_id,
            device_id=device_id,
            target_generation=2,
            state="attention_required",
            checkpoint="attention_required",
            failure_code="sensor_reset_failed",
            prepared_receipt=unsafe_receipt,
            prepared_receipt_digest="a" * 64,
        )


def test_shared_reset_contract_advertisement_and_optional_generation_fields() -> None:
    device_api = yaml.safe_load(
        (ROOT / "shared" / "openapi" / "device-api.yaml").read_text(encoding="utf-8")
    )
    ingest_api = yaml.safe_load(
        (ROOT / "shared" / "openapi" / "device-ingest-api.yaml").read_text(encoding="utf-8")
    )
    server_api = yaml.safe_load(
        (ROOT / "shared" / "openapi" / "server-api.yaml").read_text(encoding="utf-8")
    )
    assert device_api["info"]["x-data-reset-protocol"] == "data-reset/1.0.0"
    assert ingest_api["info"]["x-data-reset-protocol"] == "data-reset/1.0.0"
    assert server_api["info"]["x-data-reset-protocol"] == "data-reset/1.0.0"
    assert {
        "/api/v1/data-reset/prepare",
        "/api/v1/data-reset/commit",
        "/api/v1/data-reset/status",
        "/api/v1/data-reset/cancel",
    } <= set(device_api["paths"])
    assert "202" in device_api["paths"]["/api/v1/data-reset/status"]["get"]["responses"]
    assert "202" in device_api["paths"]["/api/v1/data-reset/cancel"]["post"]["responses"]
    assert {
        "/api/v1/system/data-reset/plan",
        "/api/v1/system/data-reset/execute",
        "/api/v1/system/data-reset/{operation_id}",
        "/api/v1/system/data-reset/{operation_id}/retry",
        "/api/v1/system/data-reset/{operation_id}/cancel",
    } <= set(server_api["paths"])
    participant_properties = server_api["components"]["schemas"]["DataResetParticipantView"][
        "properties"
    ]
    assert participant_properties["new_sequence_floor"]["anyOf"][0]["minimum"] == 0
    assert participant_properties["new_next_sequence"]["anyOf"][0]["minimum"] == 1
    assert participant_properties["reset_boundary"]["maximum"] == 2**63 - 3
    assert participant_properties["new_sequence_floor"]["anyOf"][0]["maximum"] == (2**63 - 1)

    reset_prepare = device_api["components"]["schemas"]["DataResetPrepareRequest"]
    assert {
        "expected_build_hash",
        "expected_card_generation",
    } <= set(reset_prepare["required"])
    for field in (
        "expected_boundary",
        "server_highest_contiguous",
        "server_maximum_seen",
    ):
        assert reset_prepare["properties"][field]["maximum"] == 2**63 - 3
    assert (
        device_api["components"]["schemas"]["DataResetCommitRequest"]["properties"][
            "approved_boundary"
        ]["maximum"]
        == 2**63 - 3
    )
    reset_receipt = device_api["components"]["schemas"]["DataResetReceipt"]
    assert reset_receipt["properties"]["failure_code"] == {
        "type": ["string", "null"],
        "maxLength": 80,
        "pattern": "^[a-z0-9][a-z0-9_]{0,79}$",
    }

    ingest_reset = ingest_api["components"]["schemas"]["DataResetHeartbeatStatus"]
    assert ingest_reset["properties"]["failure_code"] == reset_receipt["properties"]["failure_code"]
    assert ingest_reset["properties"]["reset_boundary"]["maximum"] == 2**63 - 3
    assert (
        ingest_api["components"]["schemas"]["DurableReading"]["properties"]["sequence"]["maximum"]
        == 2**63 - 1
    )

    heartbeat_schema = json.loads(
        (ROOT / "shared" / "schemas" / "heartbeat.schema.json").read_text()
    )
    batch_schema = json.loads(
        (ROOT / "shared" / "schemas" / "reading-batch.schema.json").read_text()
    )
    assert "data_generation" not in heartbeat_schema["required"]
    assert heartbeat_schema["properties"]["data_generation"]["default"] == 0
    assert heartbeat_schema["properties"]["data_reset"] == {"$ref": "#/$defs/data_reset_status"}
    assert (
        heartbeat_schema["$defs"]["data_reset_status"]["properties"]["failure_code"]
        == (reset_receipt["properties"]["failure_code"])
    )
    assert (
        heartbeat_schema["$defs"]["data_reset_status"]["properties"]["reset_boundary"]["maximum"]
        == 2**63 - 3
    )
    for field in (
        "oldest_stored_sequence",
        "oldest_syncable_sequence",
        "newest_syncable_sequence",
        "newest_stored_sequence",
        "server_ack_sequence",
        "server_maximum_seen_sequence",
    ):
        assert heartbeat_schema["properties"][field]["maximum"] == 2**63 - 1
    assert "data_generation" not in batch_schema["required"]
    assert batch_schema["properties"]["data_generation"]["default"] == 0
    reading_schema = batch_schema["properties"]["readings"]["items"]
    assert "data_generation" not in reading_schema["required"]
    assert reading_schema["properties"]["data_generation"]["default"] == 0
    assert reading_schema["properties"]["sequence"]["maximum"] == 2**63 - 1


def test_server_openapi_and_capability_normalization_advertise_exact_reset_protocol() -> None:
    server_openapi = app.openapi()
    assert server_openapi["info"]["x-data-reset-protocol"] == "data-reset/1.0.0"
    schemas = server_openapi["components"]["schemas"]
    assert (
        schemas["HeartbeatDataResetStatus"]["properties"]["reset_boundary"]["anyOf"][0]["maximum"]
        == 2**63 - 3
    )
    assert schemas["Heartbeat"]["properties"]["server_ack_sequence"]["maximum"] == 2**63 - 1
    heartbeat_failure = schemas["HeartbeatDataResetStatus"]["properties"]["failure_code"]["anyOf"][
        0
    ]
    assert heartbeat_failure["maxLength"] == 80
    assert heartbeat_failure["pattern"] == "^[a-z0-9][a-z0-9_]{0,79}$"
    assert (
        schemas["ReadingBatchResponse"]["properties"]["accepted"]["items"]["maximum"] == 2**63 - 1
    )
    assert all(
        item["maximum"] == 2**63 - 1
        for item in schemas["HeartbeatResponse"]["properties"]["gap_ranges"]["items"]["prefixItems"]
    )
    participant = DataResetParticipantView(
        device_id="b09baa6a-273f-4338-88e1-af0b47989036",
        name="Outdoor AC",
        state="verified",
        reset_generation=2,
        reset_boundary=91,
        new_sequence_floor=91,
        new_next_sequence=92,
    )
    assert participant.new_sequence_floor == 91
    assert participant.new_next_sequence == 92
    capabilities = DeviceCapabilities(
        hardware_target="esp32-s3",
        pzem_model="PZEM-004T V4.0",
        sd_present=True,
        supported_endpoints=["data-reset/1.0.0"],
        data_reset_protocol="data-reset/1.0.0",
    )
    assert capabilities.data_reset_protocol == "data-reset/1.0.0"
    stored = DeviceCapability(
        device_id="b09baa6a-273f-4338-88e1-af0b47989036",
        hardware_target="esp32-s3",
        pzem_model="PZEM-004T V4.0",
        sd_required=True,
        features={"supported_endpoints": ["data-reset/1.0.0"]},
        reported_at=datetime.now(UTC),
    )
    assert _supports_data_reset(stored)
    stored.features = {"data_reset": "data-reset/1.0.0"}
    assert _supports_data_reset(stored)


@pytest.mark.asyncio
async def test_enrollment_persists_normalized_reset_capability(
    api_client: httpx.AsyncClient, session: AsyncSession
) -> None:
    bootstrap = await api_client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "reset-capability@example.com",
            "display_name": "Reset Capability Admin",
            "password": "Production-Reset-Capability-Password-42!",
        },
    )
    assert bootstrap.status_code == 201, bootstrap.text
    site_id = (await api_client.get("/api/v1/sites")).json()[0]["id"]
    csrf_token = api_client.cookies.get("pm_csrf")
    assert csrf_token
    token = await api_client.post(
        "/api/v1/enrollment-tokens",
        headers={"X-CSRF-Token": csrf_token},
        json={"site_id": site_id, "name": "Reset-capable sensor"},
    )
    assert token.status_code == 201, token.text
    claim = await api_client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": "pm-protocol/1.0.0",
            "hardware_id": "esp32s3-reset-capability-0001",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
                "sd_required": True,
                "supported_endpoints": [
                    "/api/v1/data-reset/prepare",
                    "/api/v1/data-reset/commit",
                    "/api/v1/data-reset/status",
                    "/api/v1/data-reset/cancel",
                ],
                "data_reset_protocol": "data-reset/1.0.0",
            },
        },
    )
    assert claim.status_code == 201, claim.text
    capability = await session.get(DeviceCapability, claim.json()["device_id"])
    assert capability is not None
    assert capability.features["data_reset"] == "data-reset/1.0.0"
    assert _supports_data_reset(capability)


def test_data_reset_receipt_hmac_matches_shared_vector() -> None:
    vector = json.loads(
        (ROOT / "shared" / "auth-test-vectors" / "data-reset-receipt-v1.json").read_text(
            encoding="utf-8"
        )
    )
    secret = bytes.fromhex(vector["secret_hex"])
    receipt = vector["receipt_without_digest"]
    assert (
        derive_data_reset_receipt_key(secret, vector["device_id"]).hex()
        == vector["receipt_key_hex"]
    )
    assert canonical_data_reset_receipt(receipt).decode() == vector["canonical_json_utf8"]
    digest = calculate_data_reset_receipt_digest(secret, vector["device_id"], receipt)
    assert digest == vector["receipt_digest_hex"]
    response = SensorDataResetResponse(
        operation_id=receipt["operation_id"],
        device_id=receipt["device_id"],
        target_generation=receipt["target_generation"],
        state="prepared",
        checkpoint="prepared",
        prepared_receipt=vector["canonical_json_utf8"],
        prepared_receipt_digest=digest,
        configuration_preservation_digest_before="c" * 64,
    )
    assert "pzem_cumulative_energy_wh" not in response.prepared_receipt
    assert verify_data_reset_receipt_digest(
        secret, vector["device_id"], {**receipt, "receipt_digest": digest}
    )
    assert not verify_data_reset_receipt_digest(
        secret,
        vector["device_id"],
        {**receipt, "local_record_count": 41, "receipt_digest": digest},
    )
    unsafe_receipt = json.dumps(
        {**receipt, "pzem_cumulative_energy_wh": "42183.2"},
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(ValidationError):
        SensorDataResetResponse(
            operation_id=receipt["operation_id"],
            device_id=receipt["device_id"],
            target_generation=receipt["target_generation"],
            state="prepared",
            checkpoint="prepared",
            prepared_receipt=unsafe_receipt,
            prepared_receipt_digest=digest,
        )


def test_prepare_receipt_accepts_required_evidence_and_redacts_audit_copy() -> None:
    operation_id = "187da6e7-c0da-4f95-a2d2-740b874ed9a4"
    device_id = "b09baa6a-273f-4338-88e1-af0b47989036"
    receipt = {
        "protocol": "data-reset/1.0.0",
        "operation_id": operation_id,
        "device_id": device_id,
        "target_generation": 2,
        "sd_status": "verified",
        "newest_stored_sequence": 91,
        "newest_syncable_sequence": 90,
        "prepared_pzem_energy_wh": "42183.2",
        "commit_pzem_energy_wh": "42184.0",
        "verified_pzem_energy_wh": "42185.0",
        "software_energy_baseline_before_wh": "41800.0",
        "configuration_preservation_digest_before": "c" * 64,
    }
    canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"))

    response = SensorDataResetResponse(
        operation_id=operation_id,
        device_id=device_id,
        target_generation=2,
        state="prepared",
        checkpoint="prepared",
        prepared_receipt=canonical,
        prepared_receipt_digest="a" * 64,
    )

    assert response.prepared_receipt == canonical
    safe_audit_copy = redact_history_values(receipt)
    assert safe_audit_copy["newest_stored_sequence"] == 91
    assert safe_audit_copy["newest_syncable_sequence"] == 90
    assert safe_audit_copy["sd_status"] == "verified"
    assert safe_audit_copy["configuration_preservation_digest_before"] == "c" * 64
    assert safe_audit_copy["prepared_pzem_energy_wh"] == "[redacted-by-data-reset]"
    assert safe_audit_copy["commit_pzem_energy_wh"] == "[redacted-by-data-reset]"
    assert safe_audit_copy["verified_pzem_energy_wh"] == "[redacted-by-data-reset]"
    assert safe_audit_copy["software_energy_baseline_before_wh"] == ("[redacted-by-data-reset]")


def test_history_redaction_removes_nested_measurement_and_cost_values_only() -> None:
    payload = {
        "event_sequence": 812,
        "failure_code": "sensor_reset_timeout",
        "security_outcome": "denied",
        "configuration_preservation_digest_after": "d" * 64,
        "measurement": {
            "active_power_w": "1234.5",
            "voltage_v": "121.2",
            "current_a": "10.18",
            "raw_energy_wh": "998877",
        },
        "cost_summary": {
            "energy_kwh": "4.25",
            "total_usage_kwh": "19.5",
            "usage_by_tier": {"tier_1": "12.0"},
            "energy_subtotal": "1.42",
            "total_cost_usd": "2.03",
        },
        "samples": [{"power_avg_w": "900", "meter_energy_total_wh": "101010"}],
    }

    redacted = redact_history_values(payload)

    assert redacted["event_sequence"] == 812
    assert redacted["failure_code"] == "sensor_reset_timeout"
    assert redacted["security_outcome"] == "denied"
    assert redacted["configuration_preservation_digest_after"] == "d" * 64
    assert set(redacted["measurement"].values()) == {"[redacted-by-data-reset]"}
    assert set(redacted["cost_summary"].values()) == {"[redacted-by-data-reset]"}
    assert set(redacted["samples"][0].values()) == {"[redacted-by-data-reset]"}
