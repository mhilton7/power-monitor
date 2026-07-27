from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import Admin, CsrfPrincipal, DbSession, audit_event
from app.db.models import RateAssignment, RatePlan, RateVersion, UtilityAccount
from app.problem import ProblemError
from app.rates.documents import engine_plan
from app.rates.engine import RateEngine
from app.rates.service import version_document
from app.schemas import (
    SensorTestModeAction,
    SensorTestModePoint,
    SensorTestModeSensor,
    SensorTestModeSensorUpdate,
    SensorTestModeState,
    SensorTestModeUpdate,
    SensorTestModeWrite,
)
from app.sensor_test_mode import sensor_test_mode

router = APIRouter(prefix="/api/v1/test-mode", tags=["sensor-test-mode"])


def _require_owner(principal: Any) -> None:
    if "admin" not in principal.roles:
        raise ProblemError(
            403,
            "Owner access required",
            "Only a home owner can use Sensor Test Mode",
            "test_mode_owner_required",
        )


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _cost_preview(
    session: DbSession,
    state: dict[str, Any],
) -> dict[str, Any]:
    if not state.get("enabled") or not state.get("cost_preview_enabled"):
        state["cost_preview"] = {
            "enabled": bool(state.get("cost_preview_enabled")),
            "available": False,
            "energy_kwh": state.get("total_energy_kwh", Decimal("0")),
            "disclosure": (
                "Temporary preview is off. Synthetic readings never affect bills or saved costs."
            ),
        }
        return state
    snapshot = await sensor_test_mode.snapshot_for_cost()
    if snapshot is None:
        return state
    started_at, ended_at, energy_kwh, site_id = snapshot
    query = select(UtilityAccount).where(
        UtilityAccount.status == "active",
        UtilityAccount.archived_at.is_(None),
    )
    if site_id:
        query = query.where(UtilityAccount.site_id == site_id)
    account = await session.scalar(query.order_by(UtilityAccount.created_at).limit(1))
    if account is None:
        state["cost_preview"] = {
            "enabled": True,
            "available": False,
            "energy_kwh": energy_kwh,
            "disclosure": (
                "Temporary preview only: configure an electric service and current rate plan. "
                "No bill or finalized cost is created."
            ),
        }
        return state
    now = datetime.now(UTC)
    assignment = await session.scalar(
        select(RateAssignment)
        .where(
            RateAssignment.utility_account_id == account.id,
            RateAssignment.cancelled_at.is_(None),
            RateAssignment.effective_from <= now,
            (RateAssignment.effective_to.is_(None) | (RateAssignment.effective_to > now)),
        )
        .order_by(RateAssignment.effective_from.desc())
        .limit(1)
    )
    version_id = assignment.rate_version_id if assignment else account.active_rate_version_id
    version = await session.get(RateVersion, version_id) if version_id else None
    if version is None:
        state["cost_preview"] = {
            "enabled": True,
            "available": False,
            "energy_kwh": energy_kwh,
            "currency": account.currency,
            "disclosure": (
                "Temporary preview only: make a published rate version current to estimate "
                "synthetic energy cost. No bill or finalized cost is created."
            ),
        }
        return state
    try:
        document = await version_document(session, version)
        calculation = RateEngine(engine_plan(document)).calculate(
            start=started_at,
            end=ended_at,
            energy_kwh=energy_kwh,
            cost_scope="energy_only",
        )
        plan = await session.get(RatePlan, version.rate_plan_id)
        state["cost_preview"] = {
            "enabled": True,
            "available": True,
            "energy_kwh": energy_kwh,
            "estimated_energy_cost": calculation.total.quantize(Decimal("0.0001")),
            "currency": version.currency,
            "rate_plan": plan.name if plan else document.plan_name,
            "rate_version": version.version,
            "disclosure": (
                "Temporary test-only energy estimate using the current reviewed rate. "
                "Fixed charges are excluded; no bill, historical cost, or export is created."
            ),
        }
    except (ValueError, KeyError):
        state["cost_preview"] = {
            "enabled": True,
            "available": False,
            "energy_kwh": energy_kwh,
            "currency": account.currency,
            "rate_version": version.version,
            "disclosure": (
                "The current rate needs more billing context for a temporary preview. "
                "Synthetic data remains isolated and no saved cost was created."
            ),
        }
    return state


async def _state(session: DbSession) -> SensorTestModeState:
    return SensorTestModeState.model_validate(
        await _cost_preview(session, await sensor_test_mode.state())
    )


def _test_mode_error(exc: Exception) -> ProblemError:
    if isinstance(exc, RuntimeError):
        return ProblemError(
            409,
            "Sensor Test Mode is off",
            "Enable Sensor Test Mode before changing simulated sensors",
            "test_mode_disabled",
        )
    if isinstance(exc, LookupError):
        return ProblemError(
            404,
            "Simulated sensor not found",
            "The selected simulated sensor no longer exists",
            "test_sensor_missing",
        )
    return ProblemError(
        422,
        "Invalid test configuration",
        str(exc),
        "test_mode_configuration_invalid",
    )


@router.get("", response_model=SensorTestModeState)
async def get_test_mode(
    request: Request,
    _principal: Admin,
    session: DbSession,
) -> SensorTestModeState:
    state = await _state(session)
    expiry = await sensor_test_mode.consume_expiry_audit()
    if expiry is not None:
        session.add(
            audit_event(
                action="sensor_test_mode.expired",
                actor_type="system",
                actor_id=None,
                request=request,
                object_type="sensor_test_mode",
                object_id=expiry["session_id"],
                details=expiry,
            )
        )
        await session.commit()
    return state


@router.post("/enable", response_model=SensorTestModeState)
async def enable_test_mode(
    payload: SensorTestModeWrite,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> SensorTestModeState:
    _require_owner(principal)
    try:
        await sensor_test_mode.enable(payload)
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    session.add(
        audit_event(
            action="sensor_test_mode.enabled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_test_mode",
            details={
                "sensor_count": payload.sensor_count,
                "load_profile": payload.load_profile,
                "offline_sensor_count": len(payload.offline_sensor_indexes),
                "cost_preview_enabled": payload.cost_preview_enabled,
                "expires_in_minutes": payload.expires_in_minutes,
            },
        )
    )
    await session.commit()
    return await _state(session)


@router.put("", response_model=SensorTestModeState)
async def update_test_mode(
    payload: SensorTestModeUpdate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> SensorTestModeState:
    _require_owner(principal)
    try:
        await sensor_test_mode.update(payload)
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    session.add(
        audit_event(
            action="sensor_test_mode.updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_test_mode",
            details={
                key: value
                for key, value in payload.model_dump(
                    mode="json", exclude={"idempotency_key"}
                ).items()
                if value is not None
            },
        )
    )
    await session.commit()
    return await _state(session)


@router.post("/disable", response_model=SensorTestModeState)
async def disable_test_mode(
    payload: SensorTestModeAction,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> SensorTestModeState:
    _require_owner(principal)
    previous = await sensor_test_mode.state()
    await sensor_test_mode.disable(payload.idempotency_key)
    session.add(
        audit_event(
            action="sensor_test_mode.disabled",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_test_mode",
            object_id=previous.get("session_id"),
            details={
                "cleanup": "complete",
                "discarded_history_points": True,
                "real_data_changed": False,
            },
        )
    )
    await session.commit()
    return await _state(session)


@router.post("/reset", response_model=SensorTestModeState)
async def reset_test_mode(
    payload: SensorTestModeAction,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> SensorTestModeState:
    _require_owner(principal)
    try:
        await sensor_test_mode.reset(payload.idempotency_key)
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    session.add(
        audit_event(
            action="sensor_test_mode.reset",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="sensor_test_mode",
            details={"synthetic_history_cleared": True, "real_data_changed": False},
        )
    )
    await session.commit()
    return await _state(session)


@router.get("/sensors", response_model=list[SensorTestModeSensor])
async def test_mode_sensors(_principal: Admin) -> list[SensorTestModeSensor]:
    try:
        values = await sensor_test_mode.sensors()
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    return [SensorTestModeSensor.model_validate(value) for value in values]


@router.put("/sensors/{sensor_id}", response_model=SensorTestModeSensor)
async def update_test_sensor(
    sensor_id: str,
    payload: SensorTestModeSensorUpdate,
    request: Request,
    principal: CsrfPrincipal,
    session: DbSession,
) -> SensorTestModeSensor:
    _require_owner(principal)
    try:
        value = await sensor_test_mode.update_sensor(sensor_id, payload)
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    session.add(
        audit_event(
            action="sensor_test_mode.sensor_updated",
            actor_type="user",
            actor_id=principal.user.id,
            request=request,
            object_type="test_sensor",
            object_id=sensor_id,
            details={
                key: value
                for key, value in payload.model_dump(
                    mode="json", exclude={"idempotency_key"}
                ).items()
                if value is not None
            },
        )
    )
    await session.commit()
    return SensorTestModeSensor.model_validate(value)


@router.get("/history", response_model=list[SensorTestModePoint])
async def test_mode_history(
    _principal: Admin,
    sensor_id: str | None = None,
    limit: int = Query(default=1000, ge=1, le=10_000),
) -> list[SensorTestModePoint]:
    try:
        values = await sensor_test_mode.history(sensor_id=sensor_id, limit=limit)
    except (RuntimeError, LookupError, ValueError) as exc:
        raise _test_mode_error(exc) from exc
    return [SensorTestModePoint.model_validate(value) for value in values]
