from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


@dataclass
class ProblemError(Exception):
    status: int
    title: str
    detail: str
    code: str
    extra: dict[str, Any] | None = None


def problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "type": f"https://power-monitor.local/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        payload.update(extra)
    return JSONResponse(payload, status_code=status, media_type="application/problem+json")
