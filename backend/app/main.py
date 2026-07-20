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

from app.api.routes import auth, device_protocol, exports, firmware, logs, management, rates, system
from app.config import get_settings
from app.logging import configure_logging
from app.problem import ProblemError, problem_response
from app.security.protocol import ProtocolAuthError


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
    logger.info("application_stopped")


app = FastAPI(
    title="Power Monitor Server API",
    version="1.0.0",
    description="Central server for pm-protocol/1.0.0 sensor fleets",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

for router in (
    auth.router,
    device_protocol.router,
    logs.router,
    management.router,
    rates.router,
    firmware.router,
    exports.router,
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
    response = await call_next(request)
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
    return problem_response(
        request,
        status=422,
        title="Request validation failed",
        detail="One or more fields are invalid",
        code="validation_error",
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
