from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_FILE_BACKED_SETTINGS = (
    ("database_url", "database_url_file"),
    ("app_master_key", "app_master_key_file"),
    ("session_pepper", "session_pepper_file"),
    ("bootstrap_secret", "bootstrap_secret_file"),
)


def _read_secret(path: Path, setting_name: str) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{setting_name} secret file is not readable: {path}") from exc
    if len(payload) > 65_536:
        raise ValueError(f"{setting_name} secret file exceeds 64 KiB")
    try:
        value = payload.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{setting_name} secret file is not UTF-8") from exc
    if not value:
        raise ValueError(f"{setting_name} secret file is empty")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError(f"{setting_name} secret file must contain exactly one text line")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Power Monitor Server"
    power_monitor_version: str = "1.0.0"
    protocol_version: str = "pm-protocol/1.0.0"
    database_url: str = "postgresql+asyncpg://power_monitor:power_monitor@localhost/power_monitor"
    database_url_file: Path | None = None
    app_master_key: str = ""
    app_master_key_file: Path | None = None
    session_pepper: str = ""
    session_pepper_file: Path | None = None
    bootstrap_secret: str = ""
    bootstrap_secret_file: Path | None = None
    public_origin: str = "http://localhost:5173"
    cookie_secure: bool = True
    cookie_name: str = "pm_session"
    csrf_cookie_name: str = "pm_csrf"
    session_hours: int = 12
    heartbeat_expectation_seconds: int = 15
    max_device_clock_skew_seconds: int = 300
    max_reading_batch_records: int = 500
    max_reading_batch_bytes: int = 2_000_000
    default_site_name: str = "Upland Site"
    default_timezone: str = "America/Los_Angeles"
    default_currency: str = "USD"
    firmware_path: Path = Path("/data/firmware")
    report_path: Path = Path("/data/reports")
    backup_path: Path = Path("/data/backups")
    log_path: Path = Path("/data/logs")
    log_retention_days: int = 90
    max_log_export_bytes: int = 250 * 1024 * 1024
    log_format_version: str = "pm-log/1.0.0"
    poll_public_addresses: bool = False
    allowed_poll_ports: tuple[int, ...] = (80, 443, 8080, 8443)
    log_level: str = "INFO"

    @field_validator("log_retention_days")
    @classmethod
    def valid_log_retention(cls, value: int) -> int:
        if value != 90:
            raise ValueError("LOG_RETENTION_DAYS must remain 90")
        return value

    @field_validator("max_log_export_bytes")
    @classmethod
    def valid_log_export_limit(cls, value: int) -> int:
        if value < 1_048_576:
            raise ValueError("MAX_LOG_EXPORT_BYTES must be at least 1 MiB")
        return value

    @field_validator("public_origin")
    @classmethod
    def no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @model_validator(mode="after")
    def load_file_backed_settings(self) -> Self:
        for value_field, file_field in _FILE_BACKED_SETTINGS:
            path = getattr(self, file_field)
            if path is not None:
                setattr(self, value_field, _read_secret(path, value_field))
        return self

    @property
    def production_secrets_valid(self) -> bool:
        return bool(self.app_master_key and self.session_pepper and self.bootstrap_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
