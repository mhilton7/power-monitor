from __future__ import annotations

import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.routes import (
    access_control,
    account_network,
    agent_protocol,
    auth,
    bill_imports,
    data_reset,
    device_protocol,
    exports,
    firmware,
    interface_text,
    logs,
    management,
    rate_management,
    rates,
    site_management,
    status_indicators,
    system,
    test_mode,
    tiered_rates,
)
from app.config import get_settings
from app.logging import configure_logging
from app.problem import ProblemError, problem_response
from app.schemas import MAX_RESET_BOUNDARY, SIGNED_BIGINT_MAX
from app.security.protocol import ProtocolAuthError
from app.sensor_test_mode import sensor_test_mode
from app.upload_limits import FirmwareUploadLimitMiddleware


class PowerMonitorAPI(FastAPI):
    @staticmethod
    def _set_integer_maximum(schemas: dict[str, Any], model: str, field: str, maximum: int) -> None:
        model_schema = schemas.get(model)
        if not isinstance(model_schema, dict):
            return
        properties = model_schema.get("properties")
        if not isinstance(properties, dict):
            return
        field_schema = properties.get(field)
        if not isinstance(field_schema, dict):
            return

        def apply(value: Any) -> None:
            if isinstance(value, dict):
                if value.get("type") == "integer":
                    value["maximum"] = maximum
                for child in value.values():
                    apply(child)
            elif isinstance(value, list):
                for child in value:
                    apply(child)

        apply(field_schema)

    def openapi(self) -> dict[str, Any]:
        schema = super().openapi()
        schema.setdefault("info", {})["x-data-reset-protocol"] = "data-reset/1.0.0"
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        signed_sequence_fields = {
            "Heartbeat": (
                "oldest_stored_sequence",
                "oldest_syncable_sequence",
                "newest_syncable_sequence",
                "newest_stored_sequence",
                "server_ack_sequence",
                "server_maximum_seen_sequence",
            ),
            "SequenceCursorResponse": (
                "highest_contiguous_accepted_sequence",
                "maximum_seen_sequence",
                "next_sequence_floor",
            ),
            "Reading": ("sequence",),
            "UnavailableSequenceRange": ("start_sequence", "end_sequence"),
            "HeartbeatResponse": (
                "highest_contiguous_accepted_sequence",
                "gap_ranges",
            ),
            "RejectedReading": ("sequence",),
            "ReadingBatchResponse": (
                "accepted",
                "duplicates",
                "highest_contiguous_accepted_sequence",
                "missing_ranges",
            ),
            "DataResetPlanParticipant": (
                "sensor_ack_sequence",
                "sensor_newest_sequence",
                "old_sequence_floor",
                "old_next_sequence",
            ),
            "DataResetParticipantView": ("new_sequence_floor", "new_next_sequence"),
            "DeviceEventBatch": ("first_stored_event_sequence",),
        }
        reset_boundary_fields = {
            "HeartbeatDataResetStatus": ("reset_boundary",),
            "SequenceCursorResponse": ("reset_boundary",),
            "ReadingBatchResponse": ("reset_boundary",),
            "DataResetPlanParticipant": (
                "boundary",
                "server_highest_contiguous",
                "server_maximum_seen",
            ),
            "DataResetParticipantView": ("reset_boundary",),
        }
        for model, fields in signed_sequence_fields.items():
            for field in fields:
                self._set_integer_maximum(schemas, model, field, SIGNED_BIGINT_MAX)
        for model, fields in reset_boundary_fields.items():
            for field in fields:
                self._set_integer_maximum(schemas, model, field, MAX_RESET_BOUNDARY)
        return schema


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    settings = get_settings()
    configure_logging(
        settings.log_level,
        json_logs=True,
        log_path=settings.log_path,
        service="api",
        retention_days=settings.log_retention_days,
    )
    logger = structlog.get_logger()
    if not settings.production_secrets_valid:
        logger.warning("required_production_secrets_missing")
    logger.info("application_started", version=settings.power_monitor_version)
    yield
    await sensor_test_mode.shutdown()
    logger.info("application_stopped")


app = PowerMonitorAPI(
    title="Power Monitor Server API",
    version="1.0.0",
    description="Central server for pm-protocol/1.0.0 sensor fleets",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# Keep this inside the request-controls middleware so rejected requests retain
# the normal request ID, security headers, and structured request log. The
# limiter itself runs before FastAPI's multipart parser and temporary spooling.
app.add_middleware(FirmwareUploadLimitMiddleware)

for router in (
    auth.router,
    agent_protocol.router,
    interface_text.router,
    access_control.router,
    account_network.router,
    bill_imports.router,
    data_reset.router,
    tiered_rates.router,
    device_protocol.router,
    logs.router,
    management.router,
    rates.router,
    rate_management.router,
    site_management.router,
    status_indicators.router,
    firmware.router,
    exports.router,
    test_mode.router,
    system.router,
):
    app.include_router(router)


@app.middleware("http")
async def request_controls(request: Request, call_next: Any) -> Any:
    started = time.monotonic()
    request_id = request.headers.get("X-Request-ID", secrets.token_hex(12))[:128]
    request.state.request_id = request_id
    content_length = request.headers.get("content-length")
    settings = get_settings()
    if content_length and int(content_length) > max(
        settings.max_reading_batch_bytes, 34 * 1024 * 1024
    ):
        return problem_response(
            request,
            status=413,
            title="Request too large",
            detail="Request exceeds the configured size limit",
            code="request_too_large",
        )
    try:
        response = await call_next(request)
    except Exception as exc:
        # Preserve the exception for FastAPI's normal 500 handling, but record a
        # sanitized correlation trail. Request bodies, query strings, cookies,
        # authorization values, and device signatures are intentionally absent.
        structlog.get_logger().exception(
            "http_request_failed",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            error_type=type(exc).__name__,
            duration_ms=round((time.monotonic() - started) * 1000, 2),
        )
        raise
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/api/v1/auth") or "firmware" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    structlog.get_logger().info(
        "http_request",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round((time.monotonic() - started) * 1000, 2),
    )
    return response


@app.exception_handler(ProblemError)
async def handle_problem(request: Request, exc: ProblemError) -> JSONResponse:
    return problem_response(
        request,
        status=exc.status,
        title=exc.title,
        detail=exc.detail,
        code=exc.code,
        extra=exc.extra,
    )


@app.exception_handler(ProtocolAuthError)
async def handle_protocol_auth(request: Request, exc: ProtocolAuthError) -> JSONResponse:
    structlog.get_logger().warning(
        "device_authentication_failed", request_id=request.state.request_id, code=exc.code
    )
    return problem_response(
        request,
        status=exc.status_code,
        title="Device authentication failed",
        detail=str(exc),
        code=exc.code,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    code = "validation_error"
    detail = "One or more fields are invalid"
    if request.url.path == "/api/v2/agent/heartbeat":
        top_level_fields = {
            str(error["loc"][1])
            for error in exc.errors()
            if len(error["loc"]) > 1 and error["loc"][0] == "body"
        }
        if "latest" in top_level_fields:
            code = "agent_latest_invalid"
            detail = "The signed latest-measurement evidence is invalid"
        elif "pzem" in top_level_fields:
            code = "agent_pzem_health_invalid"
            detail = "The signed PZEM health evidence is invalid"
        elif "sd" in top_level_fields:
            code = "agent_sd_health_invalid"
            detail = "The signed SD health evidence is invalid"
        elif "sequences" in top_level_fields:
            code = "agent_sequence_evidence_invalid"
            detail = "The signed sequence evidence is invalid"
        elif "capabilities" in top_level_fields:
            code = "agent_capability_evidence_invalid"
            detail = "The signed capability evidence is invalid"
    return problem_response(
        request,
        status=422,
        title="Request validation failed",
        detail=detail,
        code=code,
        extra={"errors": errors},
    )


@app.exception_handler(IntegrityError)
async def handle_integrity(request: Request, _exc: IntegrityError) -> JSONResponse:
    return problem_response(
        request,
        status=409,
        title="Conflict",
        detail="The requested change conflicts with existing data",
        code="data_conflict",
    )
