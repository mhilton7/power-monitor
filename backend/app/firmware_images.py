from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from app.security.protocol import PROTOCOL

ESP_IMAGE_MAGIC = 0xE9
ESP_APP_DESCRIPTOR_MAGIC = 0xABCD5432
ESP32_S3_CHIP_ID = 9
ESP_IMAGE_HEADER_BYTES = 24
ESP_SEGMENT_HEADER_BYTES = 8
ESP_APP_DESCRIPTOR_BYTES = 256
ESP_IMAGE_HASH_BYTES = 32
MAX_ESP_IMAGE_SEGMENTS = 16
EXPECTED_PROJECT_NAME = "power-monitor-sensor"
EXPECTED_HARDWARE_TARGET = "esp32-s3"

_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_BUILD_IDENTIFIER = r"[0-9A-Za-z-]+"
_SEMVER = re.compile(
    rf"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    rf"(?:-({_PRERELEASE_IDENTIFIER}(?:\.{_PRERELEASE_IDENTIFIER})*))?"
    rf"(?:\+({_BUILD_IDENTIFIER}(?:\.{_BUILD_IDENTIFIER})*))?$"
)


class FirmwareImageError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ParsedFirmwareImage:
    version: str
    project_name: str
    hardware_target: str
    protocol_min: str
    protocol_max: str
    size_bytes: int
    build_hash: str
    build_timestamp: datetime
    image_hash: str
    segment_count: int
    chip_id: int


def _read_exact(stream: BinaryIO, length: int, *, detail: str) -> bytes:
    payload = stream.read(length)
    if len(payload) != length:
        raise FirmwareImageError("firmware_image_invalid", detail)
    return payload


def _descriptor_text(payload: bytes, field: str) -> str:
    value, separator, remainder = payload.partition(b"\x00")
    if not separator or any(remainder):
        raise FirmwareImageError(
            "firmware_image_invalid", f"Application descriptor {field} is not bounded"
        )
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FirmwareImageError(
            "firmware_image_invalid", f"Application descriptor {field} is not ASCII"
        ) from exc
    if not decoded or any(ord(character) < 0x20 or ord(character) > 0x7E for character in decoded):
        raise FirmwareImageError(
            "firmware_image_invalid", f"Application descriptor {field} is invalid"
        )
    return decoded


def _build_timestamp(date_text: str, time_text: str) -> datetime:
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%b %d %Y %H:%M:%S")
    except ValueError as exc:
        raise FirmwareImageError(
            "firmware_image_invalid", "Application descriptor build timestamp is invalid"
        ) from exc
    return parsed.replace(tzinfo=UTC)


def _contains_protocol(stream: BinaryIO, size_bytes: int) -> bool:
    marker = PROTOCOL.encode("ascii")
    overlap = len(marker) - 1
    previous = b""
    stream.seek(0)
    remaining = size_bytes
    while remaining:
        chunk = stream.read(min(65_536, remaining))
        if not chunk:
            break
        if marker in previous + chunk:
            return True
        previous = (previous + chunk)[-overlap:]
        remaining -= len(chunk)
    return False


def parse_esp32s3_application_image(
    path: Path,
    *,
    maximum_bytes: int,
    ota_partition_size_bytes: int,
    expected_project_name: str = EXPECTED_PROJECT_NAME,
) -> ParsedFirmwareImage:
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise FirmwareImageError("firmware_image_invalid", "Firmware image is unavailable") from exc
    if size_bytes <= 0:
        raise FirmwareImageError("firmware_image_invalid", "Firmware image is empty")
    if size_bytes > maximum_bytes or size_bytes > ota_partition_size_bytes:
        raise FirmwareImageError(
            "firmware_too_large", "Firmware image does not fit the configured OTA partition"
        )

    with path.open("rb") as stream:
        header = _read_exact(
            stream, ESP_IMAGE_HEADER_BYTES, detail="Firmware image header is truncated"
        )
        if header[0] != ESP_IMAGE_MAGIC:
            raise FirmwareImageError("firmware_image_invalid", "ESP image magic is invalid")
        segment_count = header[1]
        if not 1 <= segment_count <= MAX_ESP_IMAGE_SEGMENTS:
            raise FirmwareImageError("firmware_image_invalid", "ESP image segment count is invalid")
        if header[2] > 3:
            raise FirmwareImageError("firmware_image_invalid", "ESP image SPI mode is invalid")
        chip_id = struct.unpack_from("<H", header, 12)[0]
        if chip_id != ESP32_S3_CHIP_ID:
            raise FirmwareImageError(
                "firmware_wrong_target", "Firmware image is not built for ESP32-S3"
            )
        if header[23] != 1:
            raise FirmwareImageError(
                "firmware_checksum_invalid", "Firmware image must include its appended SHA-256"
            )

        checksum = 0xEF
        descriptor: bytes | None = None
        for index in range(segment_count):
            segment_header = _read_exact(
                stream,
                ESP_SEGMENT_HEADER_BYTES,
                detail=f"Firmware segment {index} header is truncated",
            )
            _load_address, segment_size = struct.unpack("<II", segment_header)
            if segment_size == 0 or segment_size > size_bytes:
                raise FirmwareImageError(
                    "firmware_image_invalid", f"Firmware segment {index} length is invalid"
                )
            if stream.tell() + segment_size > size_bytes:
                raise FirmwareImageError(
                    "firmware_image_invalid", f"Firmware segment {index} is truncated"
                )
            remaining = segment_size
            first_segment_prefix = bytearray()
            while remaining:
                chunk = _read_exact(
                    stream,
                    min(65_536, remaining),
                    detail=f"Firmware segment {index} is truncated",
                )
                if index == 0 and len(first_segment_prefix) < ESP_APP_DESCRIPTOR_BYTES:
                    needed = ESP_APP_DESCRIPTOR_BYTES - len(first_segment_prefix)
                    first_segment_prefix.extend(chunk[:needed])
                for byte in chunk:
                    checksum ^= byte
                remaining -= len(chunk)
            if index == 0:
                descriptor = bytes(first_segment_prefix)

        if descriptor is None or len(descriptor) < ESP_APP_DESCRIPTOR_BYTES:
            raise FirmwareImageError(
                "firmware_not_application_image", "ESP application descriptor is missing"
            )
        if struct.unpack_from("<I", descriptor, 0)[0] != ESP_APP_DESCRIPTOR_MAGIC:
            raise FirmwareImageError(
                "firmware_not_application_image",
                "Image is not an ESP application image at an OTA partition offset",
            )

        image_without_padding = stream.tell()
        checksum_offset = ((image_without_padding // 16) + 1) * 16 - 1
        padding_length = checksum_offset - image_without_padding
        padding = _read_exact(
            stream, padding_length, detail="Firmware checksum padding is truncated"
        )
        if any(padding):
            raise FirmwareImageError(
                "firmware_image_invalid", "Firmware checksum padding is invalid"
            )
        stored_checksum = _read_exact(stream, 1, detail="Firmware image checksum is missing")[0]
        if stored_checksum != checksum:
            raise FirmwareImageError(
                "firmware_checksum_invalid", "Firmware image checksum does not match"
            )
        stored_image_hash = _read_exact(
            stream, ESP_IMAGE_HASH_BYTES, detail="Firmware image SHA-256 is truncated"
        )
        if stream.read(1):
            raise FirmwareImageError(
                "firmware_image_invalid", "Firmware image contains unexpected trailing data"
            )
        stream.seek(0)
        image_hasher = hashlib.sha256()
        remaining = checksum_offset + 1
        while remaining:
            chunk = _read_exact(
                stream, min(65_536, remaining), detail="Firmware image is truncated"
            )
            image_hasher.update(chunk)
            remaining -= len(chunk)
        calculated_image_hash = image_hasher.digest()
        if calculated_image_hash != stored_image_hash:
            raise FirmwareImageError(
                "firmware_checksum_invalid", "Firmware appended SHA-256 does not match"
            )

        version = _descriptor_text(descriptor[16:48], "version")
        if not _SEMVER.fullmatch(version):
            raise FirmwareImageError(
                "firmware_version_invalid", "Firmware application version is not semantic"
            )
        project_name = _descriptor_text(descriptor[48:80], "project name")
        if project_name != expected_project_name:
            raise FirmwareImageError(
                "firmware_project_mismatch",
                f"Firmware project must be {expected_project_name}",
            )
        build_time = _descriptor_text(descriptor[80:96], "build time")
        build_date = _descriptor_text(descriptor[96:112], "build date")
        build_hash_bytes = descriptor[144:176]
        if not any(build_hash_bytes):
            raise FirmwareImageError(
                "firmware_image_invalid", "Firmware application build hash is missing"
            )
        if not _contains_protocol(stream, size_bytes):
            raise FirmwareImageError(
                "firmware_protocol_invalid", f"Firmware does not declare {PROTOCOL}"
            )

    return ParsedFirmwareImage(
        version=version,
        project_name=project_name,
        hardware_target=EXPECTED_HARDWARE_TARGET,
        protocol_min=PROTOCOL,
        protocol_max=PROTOCOL,
        size_bytes=size_bytes,
        build_hash=build_hash_bytes.hex(),
        build_timestamp=_build_timestamp(build_date, build_time),
        image_hash=calculated_image_hash.hex(),
        segment_count=segment_count,
        chip_id=chip_id,
    )
