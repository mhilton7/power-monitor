from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Power Monitor Server"
    power_monitor_version: str = "1.0.0"
    protocol_version: str = "pm-protocol/1.0.0"
    database_url: str = "postgresql+asyncpg://power_monitor:power_monitor@localhost/power_monitor"
    app_master_key: str = ""
    session_pepper: str = ""
    bootstrap_secret: str = ""
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
    poll_public_addresses: bool = False
    allowed_poll_ports: tuple[int, ...] = (80, 443, 8080, 8443)
    log_level: str = "INFO"

    @field_validator("public_origin")
    @classmethod
    def no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def production_secrets_valid(self) -> bool:
        return bool(self.app_master_key and self.session_pepper and self.bootstrap_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()
