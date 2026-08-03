from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import Request

from app.config import Settings, get_settings
from app.problem import problem_response

FIRMWARE_UPLOAD_PATH = "/api/v1/firmware-releases"
# Multipart framing should stay tiny for the one-field firmware endpoint. This
# allowance admits a maximum-size image with normal headers while bounding
# attacker-controlled field and filename metadata before Starlette spools it.
FIRMWARE_MULTIPART_OVERHEAD_BYTES = 64 * 1024

AsgiMessage = MutableMapping[str, Any]
AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
AsgiSend = Callable[[AsgiMessage], Awaitable[None]]
AsgiApp = Callable[[dict[str, Any], AsgiReceive, AsgiSend], Awaitable[None]]
SettingsProvider = Callable[[], Settings]


class _FirmwareUploadTooLarge(Exception):
    pass


class FirmwareUploadLimitMiddleware:
    """Bound the firmware multipart body before FastAPI parses/spools it."""

    def __init__(
        self,
        app: AsgiApp,
        settings_provider: SettingsProvider | None = None,
    ) -> None:
        self.app = app
        self.settings_provider = settings_provider

    def _settings(self, scope: dict[str, Any]) -> Settings:
        if self.settings_provider is not None:
            return self.settings_provider()
        application = scope.get("app")
        overrides = getattr(application, "dependency_overrides", {})
        provider = overrides.get(get_settings, get_settings)
        return provider()

    @staticmethod
    def _content_length(scope: dict[str, Any]) -> int | None:
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() != b"content-length":
                continue
            try:
                decoded = raw_value.decode("ascii")
            except UnicodeDecodeError as exc:
                raise _FirmwareUploadTooLarge from exc
            if not decoded.isdigit():
                raise _FirmwareUploadTooLarge
            return int(decoded)
        return None

    async def _reject(
        self,
        scope: dict[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        request = Request(scope, receive=receive)
        response = problem_response(
            request,
            status=413,
            title="Firmware too large",
            detail="Firmware upload exceeds the configured size limit",
            code="firmware_too_large",
        )
        await response(scope, receive, send)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != FIRMWARE_UPLOAD_PATH
        ):
            await self.app(scope, receive, send)
            return

        settings = self._settings(scope)
        maximum_body_bytes = settings.firmware_max_bytes + FIRMWARE_MULTIPART_OVERHEAD_BYTES
        try:
            content_length = self._content_length(scope)
            if content_length is not None and content_length > maximum_body_bytes:
                raise _FirmwareUploadTooLarge

            if content_length is not None:
                # HTTP framing makes Content-Length authoritative. Let Starlette
                # stream an admitted request into its normal multipart parser.
                await self.app(scope, receive, send)
                return

            # A chunked/unknown-length upload has no preflight size to trust.
            # Read it into a strictly bounded in-memory buffer before invoking
            # Starlette so an over-limit receive is never translated into the
            # multipart parser's generic HTTP 400 response.
            buffered: list[AsgiMessage] = []
            received_bytes = 0
            while True:
                message = await receive()
                buffered.append(message)
                if message.get("type") == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > maximum_body_bytes:
                        raise _FirmwareUploadTooLarge
                    if not message.get("more_body", False):
                        break
                elif message.get("type") == "http.disconnect":
                    break

            position = 0

            async def replay_receive() -> AsgiMessage:
                nonlocal position
                if position < len(buffered):
                    message = buffered[position]
                    position += 1
                    return message
                return {"type": "http.disconnect"}

            await self.app(scope, replay_receive, send)
        except _FirmwareUploadTooLarge:
            await self._reject(scope, receive, send)
