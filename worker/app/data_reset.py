from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from app.config import Settings
from app.data_reset.sensor_client import (
    SensorResetCommunicationError,
    request_sensor_reset,
)
from app.data_reset.service import (
    DATA_RESET_PROTOCOL,
    MAX_RESET_BOUNDARY,
    _supports_data_reset,
    perform_central_reset,
    queue_reset_backup,
    redact_history_values,
    reset_backup_verification_is_conclusive,
    restore_precommit_quarantine,
)
from app.db.models import (
    AggregateMember,
    AggregateSet,
    AuditEvent,
    BackupRun,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    DataResetOperation,
    DataResetParticipant,
    DataResetPlan,
    DataResetPricingBaseline,
    Device,
    DeviceCapability,
    DeviceDataState,
    NormalizedInterval,
    RawReading,
    RateVersion,
    SyncCursor,
    TierAllocationSegment,
    new_uuid,
)
from app.problem import ProblemError
from app.rates.engine import RateEngine
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

_MAX_SIGNED_SEQUENCE = 2**63 - 1
_MAX_UNSIGNED_COUNTER = 2**64 - 1


def _audit(
    session: AsyncSession,
    *,
    action: str,
    operation: DataResetOperation,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            id=new_uuid(),
            occurred_at=datetime.now(UTC),
            actor_type="system",
            actor_id=None,
            action=action,
            object_type="data_reset_operation",
            object_id=operation.id,
            source_ip=None,
            outcome=outcome,
            correlation_id=f"data-reset:{operation.id}",
            details=details or {},
        )
    )


def _transition(operation: DataResetOperation, state: str, now: datetime) -> None:
    if operation.state == state:
        return
    operation.state = state
    operation.revision += 1
    operation.updated_at = now


def _prepare_payload(
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
    device: Device,
) -> dict[str, Any]:
    reset_timestamp = operation.reset_timestamp
    if reset_timestamp.tzinfo is None or reset_timestamp.utcoffset() is None:
        reset_timestamp = reset_timestamp.replace(tzinfo=UTC)
    else:
        reset_timestamp = reset_timestamp.astimezone(UTC)
    strict_planned_identity = participant.planned_classification in {
        "connected",
        "authentication_failed",
    }
    expected_firmware_version = (
        participant.firmware_version
        if strict_planned_identity
        else device.firmware_version
    )
    expected_build_hash = (
        participant.firmware_build_hash
        if strict_planned_identity
        else device.firmware_build_hash
    )
    return {
        "protocol": DATA_RESET_PROTOCOL,
        "operation_id": operation.id,
        "device_id": device.id,
        "target_generation": operation.reset_generation,
        "reset_timestamp": reset_timestamp.isoformat().replace("+00:00", "Z"),
        "plan_revision": operation.plan_revision,
        "plan_digest": plan.plan_fingerprint,
        "categories": ["measurement_history"],
        "expected_boundary": participant.reset_boundary,
        "server_highest_contiguous": participant.server_highest_contiguous,
        "server_maximum_seen": participant.server_maximum_seen,
        "expected_firmware_version": expected_firmware_version or "unknown",
        "expected_build_hash": expected_build_hash,
        "expected_card_generation": participant.card_generation,
    }


def _cancel_payload(
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
) -> dict[str, Any]:
    return {
        "protocol": DATA_RESET_PROTOCOL,
        "operation_id": operation.id,
        "device_id": participant.device_id,
        "target_generation": operation.reset_generation,
        "plan_revision": operation.plan_revision,
        "plan_digest": plan.plan_fingerprint,
    }


def _commit_payload(
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
) -> dict[str, Any]:
    if participant.prepare_receipt_digest is None:
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_missing",
            "Sensor commit requires its verified prepared receipt",
            retryable=False,
        )
    return {
        "protocol": DATA_RESET_PROTOCOL,
        "operation_id": operation.id,
        "device_id": participant.device_id,
        "target_generation": operation.reset_generation,
        "plan_revision": operation.plan_revision,
        "plan_digest": plan.plan_fingerprint,
        "approved_boundary": participant.reset_boundary,
        "prepared_receipt_digest": participant.prepare_receipt_digest,
    }


def _receipt_boundary(receipt: dict[str, Any], current: int) -> int:
    values = [current]
    for key in (
        "reset_boundary",
        "sequence_floor",
        "server_ack_sequence",
        "server_maximum_seen",
        "newest_stored_sequence",
        "newest_syncable_sequence",
    ):
        value = receipt.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            values.append(value)
    next_sequence = receipt.get("next_sequence")
    if isinstance(next_sequence, int) and not isinstance(next_sequence, bool):
        values.append(max(0, next_sequence - 1))
    return max(values)


def _required_receipt_integer(
    receipt: dict[str, Any],
    field: str,
    *,
    positive: bool = False,
    maximum: int = _MAX_SIGNED_SEQUENCE,
) -> int:
    value = receipt.get(field)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < (1 if positive else 0)
        or value > maximum
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_receipt_incomplete",
            f"Sensor reset receipt omitted valid {field} evidence",
            retryable=False,
        )
    return value


def _expected_prepare_firmware(
    participant: DataResetParticipant, device: Device
) -> tuple[str | None, str | None]:
    if participant.planned_classification in {"connected", "authentication_failed"}:
        return participant.firmware_version, participant.firmware_build_hash
    return device.firmware_version, device.firmware_build_hash


def _validate_connected_plan_snapshot(
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
    receipt: dict[str, Any],
) -> None:
    """Require a planned-connected sensor to prepare the exact approved snapshot."""

    if (
        operation.central_commit_at is not None
        or participant.planned_classification != "connected"
    ):
        return
    planned = next(
        (
            item
            for item in plan.plan_snapshot.get("participants", [])
            if isinstance(item, dict) and item.get("device_id") == participant.device_id
        ),
        None,
    )
    if not isinstance(planned, dict):
        raise SensorResetCommunicationError(
            "data_reset_plan_stale",
            "The approved sensor inventory is no longer available",
            retryable=False,
        )
    expected_values = {
        "reset_boundary": planned.get("boundary"),
        "local_records_before": planned.get("local_record_count"),
        "backlog_before": planned.get("backlog_estimate"),
        "prepare_drain_records_added": planned.get("prepare_drain_records_projected"),
        "prepare_drain_first_sequence": planned.get(
            "prepare_drain_first_sequence_projected"
        ),
        "prepare_drain_last_sequence": planned.get(
            "prepare_drain_last_sequence_projected"
        ),
        "prepare_drain_syncable_records_added": planned.get(
            "prepare_drain_syncable_records_projected"
        ),
    }
    if planned.get("probe_status") == "authenticated":
        expected_values["newest_syncable_sequence"] = planned.get(
            "sensor_newest_syncable_sequence"
        )
    if any(
        field not in receipt or receipt[field] != value
        for field, value in expected_values.items()
    ):
        raise SensorResetCommunicationError(
            "data_reset_plan_stale",
            "Sensor boundary or exact local deletion counts changed after confirmation",
            retryable=False,
        )


def _validate_prepared_receipt(
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
    device: Device,
    response: dict[str, Any],
) -> dict[str, Any]:
    receipt = response.get("_prepared_receipt_parsed")
    if not isinstance(receipt, dict):
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_missing",
            "Sensor prepared without a durable HMAC-covered receipt",
            retryable=False,
        )
    expected_version, expected_build = _expected_prepare_firmware(participant, device)
    required_exact = {
        "protocol": DATA_RESET_PROTOCOL,
        "operation_id": operation.id,
        "device_id": participant.device_id,
        "target_generation": operation.reset_generation,
        "plan_revision": operation.plan_revision,
        "plan_digest": plan.plan_fingerprint,
        "state": "prepared",
        "checkpoint": "prepared",
    }
    if expected_version is not None:
        required_exact["firmware_version"] = expected_version
    if expected_build is not None:
        required_exact["firmware_build_hash"] = expected_build
    if any(receipt.get(key) != value for key, value in required_exact.items()):
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_mismatch",
            "Sensor prepared receipt did not match the approved operation and firmware",
            retryable=False,
        )
    for field in (
        "server_ack_sequence",
        "server_maximum_seen",
        "reset_boundary",
        "sequence_floor",
        "local_records_before",
        "local_records_after",
        "newest_stored_sequence",
        "newest_syncable_sequence",
        "backlog_before",
        "backlog_after",
        "prepare_drain_records_added",
        "prepare_drain_syncable_records_added",
        "measurement_pause_started_utc_ms",
        "prepared_pzem_energy_wh",
        "software_energy_baseline_before_wh",
    ):
        _required_receipt_integer(
            receipt,
            field,
            maximum=(
                _MAX_UNSIGNED_COUNTER
                if field
                in {"prepared_pzem_energy_wh", "software_energy_baseline_before_wh"}
                else _MAX_SIGNED_SEQUENCE
            ),
        )
    drain_records = int(receipt["prepare_drain_records_added"])
    drain_syncable_records = int(receipt["prepare_drain_syncable_records_added"])
    if drain_records > 2 or drain_syncable_records > drain_records:
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_inconclusive",
            "Sensor prepared receipt reported an invalid prepare-drain count",
            retryable=False,
        )
    if (
        "prepare_drain_first_sequence" not in receipt
        or "prepare_drain_last_sequence" not in receipt
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_inconclusive",
            "Sensor prepared receipt omitted exact prepare-drain evidence",
            retryable=False,
        )
    drain_first_sequence = receipt["prepare_drain_first_sequence"]
    drain_last_sequence = receipt["prepare_drain_last_sequence"]
    next_sequence = _required_receipt_integer(receipt, "next_sequence", positive=True)
    planned_snapshot = next(
        (
            item
            for item in plan.plan_snapshot.get("participants", [])
            if isinstance(item, dict) and item.get("device_id") == participant.device_id
        ),
        None,
    )
    durable_newest_syncable = (
        planned_snapshot.get("sensor_durable_newest_syncable_sequence")
        if isinstance(planned_snapshot, dict)
        and participant.planned_classification == "connected"
        and planned_snapshot.get("probe_status") == "authenticated"
        else None
    )
    if not (
        (
            drain_records == 0
            and drain_first_sequence is None
            and drain_last_sequence is None
            and drain_syncable_records == 0
        )
        or (
            1 <= drain_records <= 2
            and isinstance(drain_first_sequence, int)
            and not isinstance(drain_first_sequence, bool)
            and drain_first_sequence > 0
            and isinstance(drain_last_sequence, int)
            and not isinstance(drain_last_sequence, bool)
            and drain_last_sequence >= drain_first_sequence
            and drain_last_sequence - drain_first_sequence + 1 == drain_records
            and next_sequence == drain_last_sequence + 1
            and drain_last_sequence == int(receipt["newest_stored_sequence"])
            and (
                int(receipt["newest_syncable_sequence"]) == durable_newest_syncable
                if drain_syncable_records == 0 and durable_newest_syncable is not None
                else drain_syncable_records == 0
                or int(receipt["newest_syncable_sequence"])
                == drain_first_sequence + drain_syncable_records - 1
            )
        )
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_inconclusive",
            "Sensor prepared receipt omitted exact prepare-drain evidence",
            retryable=False,
        )
    trusted_boundary = max(
        participant.reset_boundary,
        *(
            int(receipt[field])
            for field in (
                "server_ack_sequence",
                "server_maximum_seen",
                "sequence_floor",
                "newest_stored_sequence",
                "newest_syncable_sequence",
            )
        ),
        next_sequence - 1,
    )
    if (
        receipt["reset_boundary"] != trusted_boundary
        or receipt["reset_boundary"] > MAX_RESET_BOUNDARY
        or receipt["local_records_after"] != receipt["local_records_before"]
        or receipt["backlog_after"] != receipt["backlog_before"]
        or receipt.get("configuration_preserved") is not True
        or receipt.get("pzem_baseline_captured") is not True
        or receipt.get("sd_status") != "verified"
        or not isinstance(receipt.get("boot_id"), str)
        or not receipt["boot_id"]
        or not isinstance(receipt.get("firmware_version"), str)
        or not receipt["firmware_version"]
        or not isinstance(receipt.get("firmware_build_hash"), str)
        or not receipt["firmware_build_hash"]
        or not isinstance(receipt.get("card_generation"), str)
        or not receipt["card_generation"]
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_prepare_receipt_inconclusive",
            "Sensor prepared receipt did not conclusively preserve readings, storage, and configuration",
            retryable=False,
        )
    _validate_connected_plan_snapshot(
        operation=operation,
        plan=plan,
        participant=participant,
        receipt=receipt,
    )
    return receipt


def _safe_receipt(response: dict[str, Any], kind: str) -> dict[str, Any]:
    parsed = response.get(f"_{kind}_receipt_parsed")
    return redact_history_values(parsed) if isinstance(parsed, dict) else {}


async def _mark_not_applicable(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    participant: DataResetParticipant,
) -> None:
    """Exclude inventory that cannot reconnect without leaving a durable gate behind."""

    now = datetime.now(UTC)
    participant.state = "not_applicable"
    participant.failure_code = None
    participant.failure_summary = None
    participant.last_attempt_at = now
    participant.updated_at = now
    state = await session.get(
        DeviceDataState, participant.device_id, with_for_update=True
    )
    if state is not None and state.active_operation_id in {None, operation.id}:
        state.ingestion_gate = "open"
        state.reset_required_on_reconnect = False
        state.active_operation_id = None
        state.updated_at = now
    _audit(
        session,
        action="data_reset.sensor_not_applicable",
        operation=operation,
        details={
            "device_id": participant.device_id,
            "planned_classification": participant.planned_classification,
        },
    )
    await session.commit()


def _validate_completed_receipt(
    participant: DataResetParticipant,
    response: dict[str, Any],
    *,
    operation: DataResetOperation | None = None,
    plan: DataResetPlan | None = None,
) -> None:
    receipt = response.get("_commit_receipt_parsed")
    if not isinstance(receipt, dict):
        raise SensorResetCommunicationError(
            "sensor_reset_commit_receipt_missing",
            "Sensor completed without a durable commit receipt",
            retryable=False,
        )
    if operation is not None and plan is not None:
        required_exact: dict[str, Any] = {
            "protocol": DATA_RESET_PROTOCOL,
            "operation_id": operation.id,
            "device_id": participant.device_id,
            "target_generation": operation.reset_generation,
            "plan_revision": operation.plan_revision,
            "plan_digest": plan.plan_fingerprint,
            "state": "completed",
            "checkpoint": "completed",
            "reset_boundary": participant.reset_boundary,
            "prepared_receipt_digest": participant.prepare_receipt_digest,
        }
        for field, expected in (
            ("firmware_version", participant.firmware_version),
            ("firmware_build_hash", participant.firmware_build_hash),
            ("boot_id", participant.boot_id),
            ("card_generation", participant.card_generation),
        ):
            if expected is not None:
                required_exact[field] = expected
        if any(receipt.get(key) != value for key, value in required_exact.items()):
            raise SensorResetCommunicationError(
                "sensor_reset_commit_receipt_mismatch",
                "Sensor completion receipt did not match the prepared operation",
                retryable=False,
            )
        for field in (
            "local_records_before",
            "local_records_after",
            "backlog_before",
            "backlog_after",
            "records_deleted",
            "reset_boundary",
            "sequence_floor",
            "server_ack_sequence",
            "server_maximum_seen",
            "prepared_pzem_energy_wh",
            "commit_pzem_energy_wh",
            "verified_pzem_energy_wh",
            "measurement_pause_started_utc_ms",
            "measurement_pause_ended_utc_ms",
        ):
            _required_receipt_integer(
                receipt,
                field,
                maximum=(
                    _MAX_UNSIGNED_COUNTER
                    if field
                    in {
                        "prepared_pzem_energy_wh",
                        "commit_pzem_energy_wh",
                        "verified_pzem_energy_wh",
                    }
                    else _MAX_SIGNED_SEQUENCE
                ),
            )
        _required_receipt_integer(receipt, "next_sequence", positive=True)
        prepared = dict(participant.prepare_receipt_safe or {})
        if (
            receipt["local_records_before"] != prepared.get("local_records_before")
            or receipt["records_deleted"] != receipt["local_records_before"]
            or receipt["backlog_before"] != prepared.get("backlog_before")
            or receipt["server_ack_sequence"] < participant.reset_boundary
            or receipt["server_maximum_seen"] < participant.reset_boundary
            or receipt["commit_pzem_energy_wh"] < receipt["prepared_pzem_energy_wh"]
            or receipt["verified_pzem_energy_wh"] < receipt["commit_pzem_energy_wh"]
            or receipt["measurement_pause_started_utc_ms"]
            != prepared.get("measurement_pause_started_utc_ms")
            or receipt["measurement_pause_ended_utc_ms"]
            < receipt["measurement_pause_started_utc_ms"]
            or receipt.get("measurement_pause_evidenced") is not True
        ):
            raise SensorResetCommunicationError(
                "sensor_reset_commit_receipt_inconclusive",
                "Sensor completion receipt did not prove exact prepared-data cleanup",
                retryable=False,
            )
    if receipt.get("configuration_preserved") is not True:
        raise SensorResetCommunicationError(
            "sensor_reset_configuration_changed",
            "Sensor could not prove configuration preservation",
            retryable=False,
        )
    if receipt.get("pzem_baseline_captured") is not True:
        raise SensorResetCommunicationError(
            "sensor_reset_energy_baseline_missing",
            "Sensor could not prove the software energy baseline was installed",
            retryable=False,
        )
    if receipt.get("local_records_after") != 0:
        raise SensorResetCommunicationError(
            "sensor_reset_readings_remain",
            "Sensor still reports local pre-reset reading records",
            retryable=False,
        )
    if receipt.get("backlog_after") != 0:
        raise SensorResetCommunicationError(
            "sensor_reset_backlog_remains",
            "Sensor still reports a pre-reset reading backlog",
            retryable=False,
        )
    inner_before = receipt.get("configuration_preservation_digest_before")
    inner_after = receipt.get("configuration_preservation_digest_after")
    if (
        not isinstance(inner_before, str)
        or not isinstance(inner_after, str)
        or inner_before != response.get("configuration_preservation_digest_before")
        or inner_after != response.get("configuration_preservation_digest_after")
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_configuration_changed",
            "Sensor completion receipt did not cover configuration preservation digests",
            retryable=False,
        )
    for field, code, summary in (
        (
            "queues_cleared",
            "sensor_reset_queues_not_cleared",
            "Sensor could not prove pending reading queues were cleared",
        ),
        (
            "exports_cleared",
            "sensor_reset_exports_not_cleared",
            "Sensor could not prove local reading exports were cleared",
        ),
        (
            "indexes_rebuilt",
            "sensor_reset_indexes_not_rebuilt",
            "Sensor could not prove reading indexes were rebuilt",
        ),
    ):
        if receipt.get(field) is not True:
            raise SensorResetCommunicationError(code, summary, retryable=False)
    floor = receipt.get("sequence_floor")
    next_sequence = receipt.get("next_sequence")
    if (
        not isinstance(floor, int)
        or isinstance(floor, bool)
        or floor < participant.reset_boundary
        or not isinstance(next_sequence, int)
        or isinstance(next_sequence, bool)
        or next_sequence <= participant.reset_boundary
    ):
        raise SensorResetCommunicationError(
            "sensor_reset_sequence_verification_failed",
            "Sensor sequence floor did not advance above the reset boundary",
            retryable=False,
        )


async def _prepare_one(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    operation_id = operation.id
    target_generation = operation.reset_generation
    device_id = participant.device_id
    prior_participant_state = participant.state
    device = await session.get(Device, device_id)
    capability = await session.get(DeviceCapability, device_id)
    if (
        device is None
        or device.revoked_at is not None
        or device.lifecycle_status != "active"
    ):
        await _mark_not_applicable(
            session,
            operation=operation,
            participant=participant,
        )
        return
    if not _supports_data_reset(capability):
        planned_connected = participant.planned_classification == "connected"
        participant.state = "attention_required" if planned_connected else "unsupported"
        participant.failure_code = (
            "sensor_reset_capability_changed"
            if planned_connected
            else "sensor_reset_unsupported"
        )
        participant.failure_summary = (
            "A sensor that was connected at confirmation no longer advertises data-reset/1.0.0"
            if planned_connected
            else "Sensor must run data-reset/1.0.0 firmware"
        )
        participant.last_attempt_at = now
        participant.updated_at = now
        state = await session.get(DeviceDataState, device_id, with_for_update=True)
        if state is not None:
            state.ingestion_gate = (
                "attention_required" if planned_connected else "pending_reconnect"
            )
            state.reset_required_on_reconnect = True
            state.updated_at = now
        await session.commit()
        return
    action: Literal["prepare", "status"] = (
        "status" if participant.state == "prepare_requested" else "prepare"
    )
    sensor_declared_prepared = False
    participant.state = "prepare_requested"
    participant.last_attempt_at = now
    participant.updated_at = now
    await session.commit()
    try:
        response = await request_sensor_reset(
            session,
            device=device,
            settings=settings,
            action=action,
            operation_id=operation_id,
            target_generation=target_generation,
            payload=(
                _prepare_payload(operation, plan, participant, device)
                if action == "prepare"
                else None
            ),
        )
        locked_participant = await session.get(
            DataResetParticipant,
            (operation_id, device_id),
            with_for_update=True,
        )
        assert locked_participant is not None
        participant = locked_participant
        response_state = str(response.get("state"))
        sensor_declared_prepared = response_state in {"prepared", "commit_authorized"}
        if response_state == "none" and action == "status":
            participant.state = "pending"
        elif response_state in {"prepared", "commit_authorized"}:
            raw_receipt = _validate_prepared_receipt(
                operation=operation,
                plan=plan,
                participant=participant,
                device=device,
                response=response,
            )
            receipt = _safe_receipt(response, "prepared")
            digest = response.get("prepared_receipt_digest")
            outer_preservation_digest = response.get(
                "configuration_preservation_digest_before"
            )
            inner_preservation_digest = (
                raw_receipt.get("configuration_preservation_digest_before")
                if isinstance(raw_receipt, dict)
                else None
            )
            if (
                not receipt
                or not isinstance(digest, str)
                or not isinstance(outer_preservation_digest, str)
                or inner_preservation_digest != outer_preservation_digest
            ):
                raise SensorResetCommunicationError(
                    "sensor_reset_prepare_receipt_missing",
                    "Sensor prepared without complete HMAC-covered preservation evidence",
                    retryable=False,
                )
            receipt_card = receipt.get("card_generation")
            if (
                participant.card_generation is not None
                and receipt_card != participant.card_generation
            ):
                raise SensorResetCommunicationError(
                    "sensor_reset_card_changed",
                    "Sensor card generation changed after planning",
                    retryable=False,
                )
            participant.reset_boundary = _receipt_boundary(
                receipt, participant.reset_boundary
            )
            receipt_floor = receipt.get("sequence_floor")
            if (
                isinstance(receipt_floor, int)
                and not isinstance(receipt_floor, bool)
                and receipt_floor >= 0
            ):
                participant.old_sequence_floor = max(
                    participant.old_sequence_floor, receipt_floor
                )
            receipt_next = receipt.get("next_sequence")
            if (
                isinstance(receipt_next, int)
                and not isinstance(receipt_next, bool)
                and receipt_next > 0
            ):
                participant.old_next_sequence = max(
                    participant.old_next_sequence, receipt_next
                )
            receipt_ack = receipt.get("server_ack_sequence")
            if (
                isinstance(receipt_ack, int)
                and not isinstance(receipt_ack, bool)
                and receipt_ack >= 0
            ):
                participant.sensor_ack_sequence = max(
                    participant.sensor_ack_sequence or 0, receipt_ack
                )
            newest_values = [
                value
                for value in (
                    receipt.get("newest_stored_sequence"),
                    receipt.get("newest_syncable_sequence"),
                )
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            ]
            if newest_values:
                participant.sensor_newest_sequence = max(
                    participant.sensor_newest_sequence or 0, *newest_values
                )
            participant.prepare_receipt_safe = receipt
            participant.prepare_receipt_digest = digest
            participant.preservation_hash_before = outer_preservation_digest
            participant.boot_id = receipt.get("boot_id")
            participant.firmware_version = str(raw_receipt["firmware_version"])
            participant.firmware_build_hash = str(raw_receipt["firmware_build_hash"])
            participant.card_generation = (
                str(receipt_card)
                if receipt_card is not None
                else participant.card_generation
            )
            participant.state = "prepared"
            participant.prepared_at = datetime.now(UTC)
            participant.failure_code = None
            participant.failure_summary = None
            _audit(
                session,
                action="data_reset.sensor_prepared",
                operation=operation,
                details={
                    "device_id": device_id,
                    "reset_boundary": participant.reset_boundary,
                    "target_generation": target_generation,
                },
            )
        elif response_state == "attention_required":
            participant.state = "attention_required"
            sensor_failure_code = response.get("failure_code")
            participant.failure_code = (
                sensor_failure_code
                if isinstance(sensor_failure_code, str)
                else "sensor_reset_attention_required"
            )
            participant.failure_summary = "Sensor reported a reset safety mismatch"
        else:
            participant.state = "prepare_requested"
        participant.updated_at = datetime.now(UTC)
        await session.commit()
    except SensorResetCommunicationError as exc:
        # Sensor communication and receipt validation failures do not mutate
        # database state. End the read transaction without expiring the
        # durable operation/plan objects needed by the resumable coordinator.
        await session.commit()
        locked_participant = await session.get(
            DataResetParticipant,
            (operation_id, device_id),
            with_for_update=True,
        )
        assert locked_participant is not None
        participant = locked_participant
        planned_connected_failure = (
            participant.planned_classification == "connected" and exc.retryable
        )
        participant.state = (
            "pending_reconnect"
            if exc.retryable and not planned_connected_failure
            else "attention_required"
        )
        participant.failure_code = exc.code
        participant.failure_summary = exc.summary
        participant.last_attempt_at = datetime.now(UTC)
        participant.updated_at = datetime.now(UTC)
        if sensor_declared_prepared and participant.prepared_at is None:
            # The receipt was not acceptable for commit, but the sensor has
            # durably armed its local gate and therefore still needs cancel.
            participant.prepared_at = datetime.now(UTC)
        if exc.request_may_have_reached_sensor:
            safe_prepare = dict(participant.prepare_receipt_safe or {})
            safe_prepare["_cancel_confirmation_required"] = True
            participant.prepare_receipt_safe = safe_prepare
        if exc.code == "data_reset_plan_stale":
            plan.invalidated_at = datetime.now(UTC)
            plan.invalidation_reason = "material_state_changed_during_prepare"
        state = await session.get(DeviceDataState, device_id, with_for_update=True)
        if state is not None and participant.planned_classification == "connected":
            state.ingestion_gate = "attention_required"
            state.reset_required_on_reconnect = True
            state.updated_at = datetime.now(UTC)
        if (
            exc.retryable
            and not planned_connected_failure
            and prior_participant_state != "pending_reconnect"
        ):
            _audit(
                session,
                action="data_reset.sensor_pending",
                operation=operation,
                outcome="warning",
                details={"device_id": device_id, "failure_code": exc.code},
            )
        await session.commit()


async def _cancel_operation(
    session: AsyncSession,
    operation: DataResetOperation,
    plan: DataResetPlan,
    settings: Settings,
) -> bool:
    participants = list(
        await session.scalars(
            select(DataResetParticipant).where(
                DataResetParticipant.operation_id == operation.id
            )
        )
    )
    all_cancelled = True
    for participant in participants:
        needs_sensor_cancel = participant.state in {
            "prepared",
            "prepare_requested",
        } or (
            participant.state == "attention_required"
            and (
                participant.prepared_at is not None
                or bool(
                    dict(participant.prepare_receipt_safe or {}).get(
                        "_cancel_confirmation_required"
                    )
                )
            )
        )
        if needs_sensor_cancel:
            device = await session.get(Device, participant.device_id)
            if device is None:
                all_cancelled = False
                continue
            try:
                response = await request_sensor_reset(
                    session,
                    device=device,
                    settings=settings,
                    action="cancel",
                    operation_id=operation.id,
                    target_generation=operation.reset_generation,
                    payload=_cancel_payload(operation, plan, participant),
                )
            except SensorResetCommunicationError as exc:
                participant.failure_code = exc.code
                participant.failure_summary = exc.summary
                participant.updated_at = datetime.now(UTC)
                all_cancelled = False
                continue
            if response.get("state") != "cancelled":
                all_cancelled = False
                continue
            participant.state = "pending"
            participant.failure_code = None
            participant.failure_summary = None
            participant.updated_at = datetime.now(UTC)
            safe_prepare = dict(participant.prepare_receipt_safe or {})
            safe_prepare.pop("_cancel_confirmation_required", None)
            participant.prepare_receipt_safe = safe_prepare
            needs_sensor_cancel = False
    if not all_cancelled:
        await session.commit()
        return False
    try:
        restored_artifact_count = restore_precommit_quarantine(
            operation=operation,
            roots=(
                settings.report_path,
                settings.log_path,
                settings.utility_bill_artifact_path,
                settings.rate_sync_artifact_path,
            ),
        )
    except ProblemError as exc:
        _transition(operation, "attention_required", datetime.now(UTC))
        operation.failure_code = exc.code
        operation.failure_summary = exc.detail
        await session.commit()
        return False
    for participant in participants:
        state = await session.get(
            DeviceDataState,
            participant.device_id,
            with_for_update=True,
        )
        if state is not None:
            state.ingestion_gate = "open"
            state.reset_required_on_reconnect = False
            state.active_operation_id = None
            state.updated_at = datetime.now(UTC)
    cancellation_evidence = dict(operation.final_evidence or {})
    cancellation_evidence["artifact_quarantine_restored"] = True
    cancellation_evidence["restored_artifact_count"] = restored_artifact_count
    operation.final_evidence = cancellation_evidence
    terminal = str(
        dict(operation.final_evidence or {}).get("cancel_terminal_state", "cancelled")
    )
    _transition(
        operation,
        terminal if terminal in {"cancelled", "failed_before_commit"} else "cancelled",
        datetime.now(UTC),
    )
    operation.completed_at = datetime.now(UTC)
    _audit(
        session,
        action="data_reset.cancelled"
        if operation.state == "cancelled"
        else "data_reset.failed_before_commit",
        operation=operation,
        outcome="warning",
        details={"failure_code": operation.failure_code},
    )
    await session.commit()
    return True


async def _prepare_operation(
    session: AsyncSession,
    operation: DataResetOperation,
    plan: DataResetPlan,
    settings: Settings,
) -> None:
    participants = list(
        await session.scalars(
            select(DataResetParticipant)
            .where(DataResetParticipant.operation_id == operation.id)
            .order_by(DataResetParticipant.device_id)
        )
    )
    for participant in participants:
        if participant.state == "unsupported" and participant.last_attempt_at is None:
            _audit(
                session,
                action="data_reset.sensor_pending",
                operation=operation,
                outcome="warning",
                details={
                    "device_id": participant.device_id,
                    "classification": participant.state,
                },
            )
        if participant.state not in {"prepared", "not_applicable"}:
            await _prepare_one(
                session,
                operation=operation,
                plan=plan,
                participant=participant,
                settings=settings,
            )
    participants = list(
        await session.scalars(
            select(DataResetParticipant).where(
                DataResetParticipant.operation_id == operation.id
            )
        )
    )
    if any(item.state == "attention_required" for item in participants):
        locked_operation = await session.get(
            DataResetOperation, operation.id, with_for_update=True
        )
        assert locked_operation is not None
        operation = locked_operation
        _transition(operation, "attention_required", datetime.now(UTC))
        operation.failure_code = "sensor_prepare_attention_required"
        operation.failure_summary = "A sensor failed a pre-reset safety check"
        await session.commit()
        return
    ready = all(
        item.state in {"prepared", "not_applicable"}
        or (
            item.planned_classification != "connected"
            and item.state in {"pending_reconnect", "unsupported", "unreachable"}
        )
        for item in participants
    )
    if ready:
        locked_operation = await session.get(
            DataResetOperation, operation.id, with_for_update=True
        )
        assert locked_operation is not None
        operation = locked_operation
        _transition(operation, "sensors_prepared", datetime.now(UTC))
        _audit(
            session,
            action="data_reset.sensors_prepared",
            operation=operation,
            details={
                "prepared": sum(item.state == "prepared" for item in participants),
                "pending": sum(item.state != "prepared" for item in participants),
            },
        )
        await session.commit()


async def _commit_one(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    participant: DataResetParticipant,
    settings: Settings,
) -> None:
    operation_id = operation.id
    target_generation = operation.reset_generation
    device_id = participant.device_id
    device = await session.get(Device, device_id)
    if (
        device is None
        or device.revoked_at is not None
        or device.lifecycle_status != "active"
    ):
        await _mark_not_applicable(
            session,
            operation=operation,
            participant=participant,
        )
        return
    if participant.state in {
        "pending_reconnect",
        "unsupported",
        "unreachable",
        "failed",
    }:
        capability = await session.get(DeviceCapability, device_id)
        if not _supports_data_reset(capability):
            return
        await _prepare_one(
            session,
            operation=operation,
            plan=plan,
            participant=participant,
            settings=settings,
        )
        locked_participant = await session.get(
            DataResetParticipant, (operation_id, device_id)
        )
        assert locked_participant is not None
        participant = locked_participant
        if participant.state != "prepared":
            return
    action: Literal["commit", "status"] = (
        "status" if participant.state == "commit_requested" else "commit"
    )
    participant.state = "commit_requested"
    participant.last_attempt_at = datetime.now(UTC)
    if participant.commit_authorized_at is None:
        participant.commit_authorized_at = datetime.now(UTC)
    device_state = await session.get(DeviceDataState, device_id)
    if device_state is not None:
        device_state.ingestion_gate = "committing"
    if action == "commit":
        _audit(
            session,
            action="data_reset.sensor_commit_requested",
            operation=operation,
            details={"device_id": device_id, "target_generation": target_generation},
        )
    await session.commit()
    try:
        response = await request_sensor_reset(
            session,
            device=device,
            settings=settings,
            action=action,
            operation_id=operation_id,
            target_generation=target_generation,
            payload=(
                _commit_payload(operation, plan, participant)
                if action == "commit"
                else None
            ),
        )
        locked_participant = await session.get(
            DataResetParticipant,
            (operation_id, device_id),
            with_for_update=True,
        )
        assert locked_participant is not None
        participant = locked_participant
        response_state = response.get("state")
        if response_state == "attention_required":
            sensor_failure_code = response.get("failure_code")
            raise SensorResetCommunicationError(
                sensor_failure_code
                if isinstance(sensor_failure_code, str)
                else "sensor_reset_attention_required",
                "Sensor reported a post-commit safety mismatch",
                retryable=False,
            )
        if response_state in {"preparing", "cancelled"}:
            raise SensorResetCommunicationError(
                "sensor_reset_commit_state_regressed",
                "Sensor reset state regressed after the central commit",
                retryable=False,
            )
        if response_state == "completed":
            _validate_completed_receipt(
                participant,
                response,
                operation=operation,
                plan=plan,
            )
            prepared_before = participant.preservation_hash_before
            before = response.get("configuration_preservation_digest_before")
            after = response.get("configuration_preservation_digest_after")
            if (
                not isinstance(prepared_before, str)
                or not isinstance(before, str)
                or not isinstance(after, str)
                or prepared_before != before
                or prepared_before != after
            ):
                raise SensorResetCommunicationError(
                    "sensor_reset_configuration_changed",
                    "Sensor configuration preservation digest changed",
                    retryable=False,
                )
            participant.commit_receipt_safe = _safe_receipt(response, "commit")
            participant.commit_receipt_digest = response.get("commit_receipt_digest")
            participant.preservation_hash_before = prepared_before
            participant.preservation_hash_after = after
            participant.new_sequence_floor = int(
                participant.commit_receipt_safe["sequence_floor"]
            )
            participant.new_next_sequence = int(
                participant.commit_receipt_safe["next_sequence"]
            )
            participant.state = "verified"
            participant.committed_at = datetime.now(UTC)
            participant.verified_at = datetime.now(UTC)
            participant.failure_code = None
            participant.failure_summary = None
            state = await session.get(DeviceDataState, device_id, with_for_update=True)
            if state is None:
                raise SensorResetCommunicationError(
                    "sensor_reset_device_state_missing",
                    "Server reset gate disappeared before sensor verification",
                    retryable=False,
                )
            completed_at = datetime.now(UTC)
            verified_boundary = int(participant.reset_boundary)
            state.data_generation = target_generation
            state.reset_boundary = max(int(state.reset_boundary), verified_boundary)
            state.generation_updated_at = completed_at
            state.last_reset_at = operation.reset_timestamp
            state.ingestion_gate = "open"
            state.reset_required_on_reconnect = False
            state.active_operation_id = None
            state.last_completed_operation_id = operation_id
            state.updated_at = completed_at
            cursor = await session.get(SyncCursor, device_id, with_for_update=True)
            if cursor is None:
                cursor = SyncCursor(
                    device_id=device_id,
                    highest_contiguous_sequence=verified_boundary,
                    maximum_seen_sequence=verified_boundary,
                    data_generation=target_generation,
                    reset_boundary=verified_boundary,
                    updated_at=completed_at,
                )
                session.add(cursor)
            else:
                cursor.highest_contiguous_sequence = max(
                    int(cursor.highest_contiguous_sequence), verified_boundary
                )
                cursor.maximum_seen_sequence = max(
                    int(cursor.maximum_seen_sequence), verified_boundary
                )
                cursor.data_generation = target_generation
                cursor.reset_boundary = max(
                    int(cursor.reset_boundary), verified_boundary
                )
                cursor.updated_at = completed_at
            _audit(
                session,
                action="data_reset.sensor_committed",
                operation=operation,
                details={
                    "device_id": device_id,
                    "sequence_floor": participant.new_sequence_floor,
                    "target_generation": target_generation,
                },
            )
            _audit(
                session,
                action="data_reset.sensor_verified",
                operation=operation,
                details={
                    "device_id": device_id,
                    "configuration_preserved": True,
                    "pzem_baseline_captured": True,
                },
            )
        elif response_state == "prepared":
            # A transient pre-authorization failure (for example a PZEM
            # capture timeout) deliberately leaves the device durably
            # prepared. Re-arm the POST path instead of polling status
            # forever; the exact commit is idempotent and payload-bound.
            sensor_failure_code = response.get("failure_code")
            participant.state = "prepared"
            participant.failure_code = (
                sensor_failure_code if isinstance(sensor_failure_code, str) else None
            )
            participant.failure_summary = (
                "Sensor remains prepared; retrying the exact commit"
            )
        else:
            participant.state = "commit_requested"
        participant.updated_at = datetime.now(UTC)
        await session.commit()
    except SensorResetCommunicationError as exc:
        # As in prepare, this is an application-level sensor failure rather
        # than a database failure; retain stable ORM state across the retry.
        await session.commit()
        locked_participant = await session.get(
            DataResetParticipant,
            (operation_id, device_id),
            with_for_update=True,
        )
        assert locked_participant is not None
        participant = locked_participant
        participant.state = "failed" if exc.retryable else "attention_required"
        participant.failure_code = exc.code
        participant.failure_summary = exc.summary
        participant.updated_at = datetime.now(UTC)
        state = await session.get(DeviceDataState, device_id, with_for_update=True)
        if state is not None:
            state.ingestion_gate = (
                "committing" if exc.retryable else "attention_required"
            )
            state.reset_required_on_reconnect = True
        await session.commit()


async def _post_reset_aggregate_map(
    session: AsyncSession,
    *,
    site_id: str,
    verified_device_ids: set[str],
    verified_circuit_ids: set[str],
) -> dict[str, str]:
    member_filter: Any = AggregateMember.device_id.in_(verified_device_ids)
    if verified_circuit_ids:
        member_filter = or_(
            member_filter,
            AggregateMember.circuit_id.in_(verified_circuit_ids),
        )
    rows = (
        await session.execute(
            select(AggregateSet.utility_account_id, AggregateSet.id)
            .join(
                AggregateMember,
                AggregateMember.aggregate_set_id == AggregateSet.id,
            )
            .where(
                AggregateSet.site_id == site_id,
                AggregateSet.utility_account_id.is_not(None),
                member_filter,
            )
            .order_by(AggregateSet.utility_account_id, AggregateSet.id)
        )
    ).tuples()
    result: dict[str, str] = {}
    for account_id, aggregate_id in rows:
        if account_id is not None:
            result.setdefault(str(account_id), str(aggregate_id))
    return result


async def _queue_post_reset_cost_runs(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    plan: DataResetPlan,
    verified_device_ids: set[str],
    required_account_ids: set[str],
    aggregate_by_account: dict[str, str],
) -> tuple[set[str], set[str]]:
    if not required_account_ids or operation.central_commit_at is None:
        return set(), set()
    bounds = (
        await session.execute(
            select(
                func.min(NormalizedInterval.interval_start),
                func.max(NormalizedInterval.interval_end),
            )
            .join(RawReading, RawReading.id == NormalizedInterval.raw_reading_id)
            .where(
                RawReading.device_id.in_(verified_device_ids),
                RawReading.data_generation == operation.reset_generation,
                RawReading.ingested_at >= operation.central_commit_at,
                NormalizedInterval.selected_energy_wh.is_not(None),
            )
        )
    ).one()
    input_start, input_end = bounds
    if input_start is None or input_end is None or input_end <= input_start:
        return set(), set()
    input_start = (
        input_start
        if input_start.tzinfo is not None
        else input_start.replace(tzinfo=UTC)
    )
    input_end = (
        input_end if input_end.tzinfo is not None else input_end.replace(tzinfo=UTC)
    )
    pricing_by_account = {
        str(item["utility_account_id"]): str(item["rate_version_id"])
        for item in plan.plan_snapshot.get("pricing", [])
        if isinstance(item, dict)
        and item.get("utility_account_id") is not None
        and item.get("rate_version_id") is not None
    }
    queued_accounts: set[str] = set()
    missing_aggregate_accounts: set[str] = set()
    for account_id in sorted(required_account_ids):
        aggregate_id = aggregate_by_account.get(account_id)
        rate_version_id = pricing_by_account.get(account_id)
        if aggregate_id is None or rate_version_id is None:
            missing_aggregate_accounts.add(account_id)
            continue
        existing = await session.scalar(
            select(CostCalculationRun.id).where(
                CostCalculationRun.utility_account_id == account_id,
                CostCalculationRun.aggregate_set_id == aggregate_id,
                CostCalculationRun.rate_version_id == rate_version_id,
                CostCalculationRun.input_start == input_start,
                CostCalculationRun.input_end == input_end,
                CostCalculationRun.status.in_(["queued", "running", "completed"]),
            )
        )
        if existing is None:
            run = CostCalculationRun(
                id=new_uuid(),
                utility_account_id=account_id,
                aggregate_set_id=aggregate_id,
                rate_version_id=rate_version_id,
                input_start=input_start,
                input_end=input_end,
                algorithm_version=RateEngine.algorithm_version,
                status="queued",
                coverage_percent=Decimal("0"),
                created_at=datetime.now(UTC),
            )
            session.add(run)
            _audit(
                session,
                action="data_reset.post_reset_cost_queued",
                operation=operation,
                details={
                    "utility_account_id": account_id,
                    "aggregate_set_id": aggregate_id,
                    "rate_version_id": rate_version_id,
                    "run_id": run.id,
                },
            )
        queued_accounts.add(account_id)
    return queued_accounts, missing_aggregate_accounts


async def _confirmed_post_reset_cost_accounts(
    session: AsyncSession,
    *,
    operation: DataResetOperation,
    verified_device_ids: set[str],
    required_account_ids: set[str],
    aggregate_by_account: dict[str, str],
) -> set[str]:
    if operation.central_commit_at is None:
        return set()
    confirmed: set[str] = set()
    for account_id in sorted(required_account_ids):
        aggregate_id = aggregate_by_account.get(account_id)
        baseline = await session.scalar(
            select(DataResetPricingBaseline).where(
                DataResetPricingBaseline.operation_id == operation.id,
                DataResetPricingBaseline.utility_account_id == account_id,
            )
        )
        if aggregate_id is None or baseline is None:
            continue
        run = await session.scalar(
            select(CostCalculationRun)
            .where(
                CostCalculationRun.utility_account_id == account_id,
                CostCalculationRun.aggregate_set_id == aggregate_id,
                CostCalculationRun.rate_version_id == baseline.rate_version_id,
                CostCalculationRun.status == "completed",
                CostCalculationRun.coverage_percent == Decimal("100"),
                CostCalculationRun.completed_at.is_not(None),
                CostCalculationRun.input_start >= operation.reset_timestamp,
            )
            .order_by(CostCalculationRun.completed_at.desc(), CostCalculationRun.id)
            .limit(1)
        )
        if run is None or run.algorithm_version != RateEngine.algorithm_version:
            continue
        results = list(
            await session.scalars(
                select(CostIntervalResult)
                .join(
                    NormalizedInterval,
                    NormalizedInterval.id == CostIntervalResult.normalized_interval_id,
                )
                .join(RawReading, RawReading.id == NormalizedInterval.raw_reading_id)
                .where(
                    CostIntervalResult.run_id == run.id,
                    CostIntervalResult.component == "energy",
                    RawReading.device_id.in_(verified_device_ids),
                    RawReading.data_generation == operation.reset_generation,
                    RawReading.ingested_at >= operation.central_commit_at,
                )
                .order_by(CostIntervalResult.interval_start, CostIntervalResult.id)
            )
        )
        if not results or any(
            item.calculation_version != RateEngine.algorithm_version
            or item.energy_kwh < 0
            or item.unrounded_cost != item.energy_kwh * item.price_per_kwh
            for item in results
        ):
            continue
        normalized_ids = {
            str(item.normalized_interval_id)
            for item in results
            if item.normalized_interval_id is not None
        }
        normalized = {
            item.id: item
            for item in await session.scalars(
                select(NormalizedInterval).where(
                    NormalizedInterval.id.in_(normalized_ids)
                )
            )
        }
        energy_by_interval: dict[str, Decimal] = {}
        for item in results:
            if item.normalized_interval_id is not None:
                energy_by_interval[item.normalized_interval_id] = (
                    energy_by_interval.get(item.normalized_interval_id, Decimal("0"))
                    + item.energy_kwh
                )
        energy_matches = bool(energy_by_interval)
        for interval_id, energy in energy_by_interval.items():
            interval = normalized.get(interval_id)
            selected_energy_wh = (
                interval.selected_energy_wh if interval is not None else None
            )
            if selected_energy_wh is None or energy != selected_energy_wh / Decimal(
                "1000"
            ):
                energy_matches = False
                break
        if not energy_matches:
            continue
        rate_version = await session.get(RateVersion, baseline.rate_version_id)
        if rate_version is None:
            continue
        if rate_version.pricing_model in {"tiered", "time_of_use_tiered"}:
            cycle = await session.scalar(
                select(BillingCycle).where(
                    BillingCycle.utility_account_id == account_id,
                    BillingCycle.starts_at == operation.reset_timestamp,
                )
            )
            if cycle is None or cycle.recalculation_version <= 0:
                continue
            segments = list(
                await session.scalars(
                    select(TierAllocationSegment)
                    .where(
                        TierAllocationSegment.billing_cycle_id == cycle.id,
                        TierAllocationSegment.rate_version_id
                        == baseline.rate_version_id,
                        TierAllocationSegment.normalized_interval_id.in_(
                            normalized_ids
                        ),
                        TierAllocationSegment.recalculation_version
                        == cycle.recalculation_version,
                    )
                    .order_by(
                        TierAllocationSegment.interval_start,
                        TierAllocationSegment.segment_order,
                    )
                )
            )
            if (
                not segments
                or segments[0].cumulative_start_kwh != Decimal("0")
                or any(
                    segment.unrounded_energy_charge
                    != segment.segment_energy_kwh * segment.price_per_kwh
                    for segment in segments
                )
            ):
                continue
        confirmed.add(account_id)
    return confirmed


async def _commit_operation(
    session: AsyncSession,
    operation: DataResetOperation,
    plan: DataResetPlan,
    settings: Settings,
) -> None:
    participants = list(
        await session.scalars(
            select(DataResetParticipant)
            .where(DataResetParticipant.operation_id == operation.id)
            .order_by(DataResetParticipant.device_id)
        )
    )
    for participant in participants:
        if participant.state not in {"verified", "not_applicable"}:
            await _commit_one(
                session,
                operation=operation,
                plan=plan,
                participant=participant,
                settings=settings,
            )
    participants = list(
        await session.scalars(
            select(DataResetParticipant).where(
                DataResetParticipant.operation_id == operation.id
            )
        )
    )
    locked_operation = await session.get(
        DataResetOperation, operation.id, with_for_update=True
    )
    assert locked_operation is not None
    operation = locked_operation
    if any(item.state == "attention_required" for item in participants):
        _transition(operation, "attention_required", datetime.now(UTC))
        operation.failure_code = "sensor_reset_attention_required"
        operation.failure_summary = "One or more sensors require manual attention"
        _audit(
            session,
            action="data_reset.failed",
            operation=operation,
            outcome="failure",
            details={"failure_code": operation.failure_code, "stage": operation.state},
        )
        await session.commit()
        return
    if any(item.state == "failed" for item in participants):
        _transition(operation, "partial_failure", datetime.now(UTC))
        operation.failure_code = "sensor_reset_partial_failure"
        operation.failure_summary = "One or more sensor reset checkpoints need retry"
        _audit(
            session,
            action="data_reset.failed",
            operation=operation,
            outcome="failure",
            details={"failure_code": operation.failure_code, "stage": operation.state},
        )
        await session.commit()
        return
    pending = [
        item
        for item in participants
        if item.state in {"pending_reconnect", "unsupported", "unreachable"}
    ]
    in_progress = [
        item
        for item in participants
        if item.state
        not in {
            "verified",
            "not_applicable",
            "pending_reconnect",
            "unsupported",
            "unreachable",
        }
    ]
    if in_progress:
        _transition(operation, "sensor_commit_running", datetime.now(UTC))
        operation.failure_code = None
        operation.failure_summary = None
        await session.commit()
        return
    verified_device_ids = {
        item.device_id for item in participants if item.state == "verified"
    }
    received_device_ids = (
        set(
            await session.scalars(
                select(RawReading.device_id)
                .where(
                    RawReading.device_id.in_(verified_device_ids),
                    RawReading.data_generation == operation.reset_generation,
                    RawReading.ingested_at >= operation.central_commit_at,
                )
                .distinct()
            )
        )
        if verified_device_ids and operation.central_commit_at is not None
        else set()
    )
    new_readings_received = bool(verified_device_ids) and (
        received_device_ids == verified_device_ids
    )
    direct_device_account_ids = {
        str(value)
        for value in await session.scalars(
            select(Device.utility_account_id).where(
                Device.id.in_(verified_device_ids),
                Device.utility_account_id.is_not(None),
            )
        )
        if value is not None
    }
    verified_circuit_ids = {
        str(value)
        for value in await session.scalars(
            select(Device.circuit_id).where(
                Device.id.in_(verified_device_ids),
                Device.circuit_id.is_not(None),
            )
        )
        if value is not None
    }
    aggregate_by_account = await _post_reset_aggregate_map(
        session,
        site_id=operation.site_id,
        verified_device_ids=verified_device_ids,
        verified_circuit_ids=verified_circuit_ids,
    )
    aggregate_account_ids = set(aggregate_by_account)
    device_account_ids = direct_device_account_ids | aggregate_account_ids
    priced_account_ids = {
        str(item["utility_account_id"])
        for item in plan.plan_snapshot.get("pricing", [])
    }
    required_cost_account_ids = device_account_ids & priced_account_ids
    cost_verification_required = bool(
        "cost_history" in operation.requested_categories and required_cost_account_ids
    )
    new_cost_calculation_confirmed = False
    confirmed_cost_account_ids: set[str] = set()
    queued_cost_account_ids: set[str] = set()
    missing_cost_aggregate_ids: set[str] = set()
    if new_readings_received and cost_verification_required:
        (
            queued_cost_account_ids,
            missing_cost_aggregate_ids,
        ) = await _queue_post_reset_cost_runs(
            session,
            operation=operation,
            plan=plan,
            verified_device_ids=verified_device_ids,
            required_account_ids=required_cost_account_ids,
            aggregate_by_account=aggregate_by_account,
        )
        confirmed_cost_account_ids = await _confirmed_post_reset_cost_accounts(
            session,
            operation=operation,
            verified_device_ids=verified_device_ids,
            required_account_ids=required_cost_account_ids,
            aggregate_by_account=aggregate_by_account,
        )
        new_cost_calculation_confirmed = (
            confirmed_cost_account_ids == required_cost_account_ids
        )
    evidence = dict(operation.final_evidence or {})
    evidence.update(
        {
            "new_readings_received": new_readings_received,
            "new_reading_device_ids": sorted(received_device_ids),
            "new_readings_status": (
                "confirmed"
                if new_readings_received
                else "pending"
                if verified_device_ids
                else "not_applicable_until_sensor_reset"
            ),
            "new_cost_calculation_confirmed": new_cost_calculation_confirmed,
            "new_cost_account_ids": sorted(confirmed_cost_account_ids),
            "queued_cost_account_ids": sorted(queued_cost_account_ids),
            "missing_cost_aggregate_ids": sorted(missing_cost_aggregate_ids),
            "required_cost_account_ids": sorted(required_cost_account_ids),
            "new_cost_status": (
                "confirmed"
                if new_cost_calculation_confirmed
                else "pending"
                if cost_verification_required
                else "not_requested_or_not_applicable"
            ),
        }
    )
    operation.final_evidence = evidence
    if missing_cost_aggregate_ids:
        _transition(operation, "attention_required", datetime.now(UTC))
        operation.failure_code = "data_reset_post_reset_cost_scope_invalid"
        operation.failure_summary = "A preserved utility account has no aggregate that can price the new sensor reading"
        _audit(
            session,
            action="data_reset.failed",
            operation=operation,
            outcome="failure",
            details={
                "failure_code": operation.failure_code,
                "utility_account_ids": sorted(missing_cost_aggregate_ids),
                "stage": "post_reset_cost_verification",
            },
        )
        await session.commit()
        return
    verification_ready = not verified_device_ids or (
        new_readings_received
        and (not cost_verification_required or new_cost_calculation_confirmed)
    )
    if not verification_ready:
        _transition(operation, "verification_running", datetime.now(UTC))
        operation.failure_code = None
        operation.failure_summary = None
        await session.commit()
        return
    target_state = (
        "completed_with_resets_pending_on_reconnect" if pending else "completed"
    )
    completion_transitioned = operation.state != target_state
    _transition(operation, target_state, datetime.now(UTC))
    if operation.completed_at is None:
        operation.completed_at = datetime.now(UTC)
    operation.failure_code = None
    operation.failure_summary = None
    if completion_transitioned:
        _audit(
            session,
            action="data_reset.completed",
            operation=operation,
            details={
                "state": operation.state,
                "verified_participants": sum(
                    item.state == "verified" for item in participants
                ),
                "pending_participants": len(pending),
                "deleted_counts": dict(operation.final_evidence or {}).get(
                    "deleted_counts", {}
                ),
            },
        )
    await session.commit()


async def process_data_reset_operations(
    session: AsyncSession, settings: Settings
) -> dict[str, Any]:
    # Manual-attention and partial-failure checkpoints are deliberately inert
    # until an administrator invokes retry or cancel. Pending reconnect
    # operations are probed only when no ordinary operation on any site can
    # make progress.
    operation = await session.scalar(
        select(DataResetOperation)
        .where(
            DataResetOperation.state.in_({"attention_required", "partial_failure"}),
            DataResetOperation.final_evidence["cancel_requested"]
            .as_boolean()
            .is_(True),
        )
        .order_by(DataResetOperation.started_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if operation is None:
        operation = await session.scalar(
            select(DataResetOperation)
            .where(
                DataResetOperation.state.not_in(
                    [
                        "completed",
                        "cancelled",
                        "failed_before_commit",
                        "attention_required",
                        "partial_failure",
                        "completed_with_resets_pending_on_reconnect",
                    ]
                )
            )
            .order_by(DataResetOperation.started_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    if operation is None:
        operation = await session.scalar(
            select(DataResetOperation)
            .where(
                DataResetOperation.state == "completed_with_resets_pending_on_reconnect"
            )
            .order_by(DataResetOperation.updated_at, DataResetOperation.started_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    if operation is None:
        return {"processed": 0}
    operation_id = operation.id
    plan = await session.get(DataResetPlan, operation.plan_id)
    if plan is None:
        operation.state = "attention_required"
        operation.failure_code = "data_reset_plan_missing"
        operation.failure_summary = "Durable reset plan is missing"
        await session.commit()
        return {"processed": 1, "operation_id": operation_id, "state": operation.state}
    if operation.central_commit_at is not None and dict(
        operation.final_evidence or {}
    ).get("_quarantine_journal"):
        try:
            await perform_central_reset(
                session,
                operation=operation,
                report_root=settings.report_path,
                log_root=settings.log_path,
                bill_artifact_root=settings.utility_bill_artifact_path,
                rate_artifact_root=settings.rate_sync_artifact_path,
                backup_root=settings.backup_path,
            )
        except ProblemError as exc:
            failed_operation = await session.get(
                DataResetOperation, operation_id, with_for_update=True
            )
            assert failed_operation is not None
            _transition(failed_operation, "attention_required", datetime.now(UTC))
            failed_operation.failure_code = exc.code
            failed_operation.failure_summary = exc.detail
            _audit(
                session,
                action="data_reset.failed",
                operation=failed_operation,
                outcome="failure",
                details={
                    "failure_code": exc.code,
                    "stage": "postcommit_artifact_cleanup",
                    "central_commit_completed": True,
                },
            )
            await session.commit()
            return {
                "processed": 1,
                "operation_id": operation_id,
                "state": failed_operation.state,
            }
        refreshed_operation = await session.get(
            DataResetOperation, operation_id, with_for_update=True
        )
        assert refreshed_operation is not None
        operation = refreshed_operation
    evidence = dict(operation.final_evidence or {})
    if evidence.get("cancel_requested"):
        await _cancel_operation(session, operation, plan, settings)
        return {"processed": 1, "operation_id": operation_id, "state": operation.state}
    if operation.state in {"preparing_sensors", "awaiting_confirmation"}:
        await _prepare_operation(session, operation, plan, settings)
    elif operation.state == "sensors_prepared":
        if operation.backup_mode == "verified_backup":
            try:
                await queue_reset_backup(session, operation, now=datetime.now(UTC))
                _audit(
                    session,
                    action="data_reset.backup_requested",
                    operation=operation,
                    details={"backup_id": operation.backup_run_id},
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
        else:
            _transition(operation, "backup_verified", datetime.now(UTC))
            _audit(
                session,
                action="data_reset.no_backup_confirmed",
                operation=operation,
                outcome="warning",
                details={"recoverable": False},
            )
            await session.commit()
    elif operation.state == "backup_running":
        backup = (
            await session.get(BackupRun, operation.backup_run_id)
            if operation.backup_run_id
            else None
        )
        if backup is None:
            operation.failure_code = "data_reset_backup_missing"
            operation.failure_summary = "The reset backup record is missing"
            evidence = dict(operation.final_evidence or {})
            evidence.update(
                {
                    "cancel_requested": True,
                    "cancel_terminal_state": "failed_before_commit",
                }
            )
            operation.final_evidence = evidence
            _transition(operation, "preparing_sensors", datetime.now(UTC))
            await session.commit()
        elif reset_backup_verification_is_conclusive(backup):
            assert backup is not None
            operation.backup_checksum = backup.manifest_hash
            operation.backup_verified_at = backup.verified_at
            operation.backup_reference = backup.path or backup.id
            _transition(operation, "backup_verified", datetime.now(UTC))
            _audit(
                session,
                action="data_reset.backup_verified",
                operation=operation,
                details={
                    "backup_id": backup.id,
                    "manifest_hash": backup.manifest_hash,
                    "size_bytes": backup.size_bytes,
                },
            )
            await session.commit()
        elif backup.status == "verified":
            operation.failure_code = "data_reset_backup_verification_inconclusive"
            operation.failure_summary = "The isolated restore verifier did not record conclusive backup evidence"
            evidence = dict(operation.final_evidence or {})
            evidence.update(
                {
                    "cancel_requested": True,
                    "cancel_terminal_state": "failed_before_commit",
                }
            )
            operation.final_evidence = evidence
            _transition(operation, "preparing_sensors", datetime.now(UTC))
            await session.commit()
        elif backup.status in {
            "backup_failed",
            "verification_failed",
            "failed",
        }:
            operation.failure_code = (
                backup.safe_error_code or "data_reset_backup_failed"
            )
            operation.failure_summary = (
                backup.safe_error_summary
                or "Verified reset backup could not be created"
            )
            evidence = dict(operation.final_evidence or {})
            evidence.update(
                {
                    "cancel_requested": True,
                    "cancel_terminal_state": "failed_before_commit",
                }
            )
            operation.final_evidence = evidence
            _transition(operation, "preparing_sensors", datetime.now(UTC))
            await session.commit()
    elif operation.state in {"backup_verified", "database_reset_running"}:
        try:
            await perform_central_reset(
                session,
                operation=operation,
                report_root=settings.report_path,
                log_root=settings.log_path,
                bill_artifact_root=settings.utility_bill_artifact_path,
                rate_artifact_root=settings.rate_sync_artifact_path,
                backup_root=settings.backup_path,
            )
        except ProblemError as exc:
            failed_operation = await session.get(
                DataResetOperation, operation_id, with_for_update=True
            )
            assert failed_operation is not None
            if failed_operation.state != "attention_required":
                _transition(failed_operation, "attention_required", datetime.now(UTC))
                failed_operation.failure_code = exc.code
                failed_operation.failure_summary = exc.detail
            _audit(
                session,
                action="data_reset.failed",
                operation=failed_operation,
                outcome="failure",
                details={
                    "failure_code": failed_operation.failure_code or exc.code,
                    "stage": "central_reset",
                    "central_commit_completed": (
                        failed_operation.central_commit_at is not None
                    ),
                },
            )
            await session.commit()
            operation = failed_operation
    elif operation.state == "database_reset_committed":
        _transition(operation, "sensor_commit_running", datetime.now(UTC))
        await session.commit()
    elif (
        operation.state == "verification_running"
        and operation.central_commit_at is not None
    ):
        await _commit_operation(session, operation, plan, settings)
    elif (
        operation.state
        in {"sensor_commit_running", "completed_with_resets_pending_on_reconnect"}
        and operation.central_commit_at is not None
    ):
        await _commit_operation(session, operation, plan, settings)
    refreshed = await session.get(DataResetOperation, operation_id)
    return {
        "processed": 1,
        "operation_id": operation_id,
        "state": refreshed.state if refreshed is not None else "attention_required",
    }
