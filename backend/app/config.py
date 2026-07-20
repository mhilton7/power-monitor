from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.rates.schedule import schedule_parts

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
    rate_sync_artifact_path: Path = Path("/app/data/rate-source-artifacts")
    rate_sync_enabled: bool = True
    rate_sync_cron: str = "15 3 * * 0"
    rate_sync_timezone: str = "America/Los_Angeles"
    rate_sync_jitter_minutes: int = 20
    rate_sync_policy: str = "manual_review"
    rate_sync_max_source_bytes: int = 10_485_760
    rate_sync_connect_timeout_seconds: int = 10
    rate_sync_read_timeout_seconds: int = 30
    rate_sync_total_timeout_seconds: int = 45
    rate_sync_max_redirects: int = 3
    rate_sync_max_retries: int = 3
    rate_sync_allowed_hosts: str = "www.sce.com,sce.com"
    rate_sync_auto_max_percent_change: int = 25
    rate_sync_retroactive_auto_days: int = 0
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

    @field_validator("rate_sync_jitter_minutes")
    @classmethod
    def valid_rate_jitter(cls, value: int) -> int:
        if not 0 <= value <= 20:
            raise ValueError("RATE_SYNC_JITTER_MINUTES must be between 0 and 20")
        return value

    @field_validator("rate_sync_policy")
    @classmethod
    def valid_rate_sync_policy(cls, value: str) -> str:
        if value not in {"manual_review", "notify_only", "auto_activate_verified"}:
            raise ValueError("RATE_SYNC_POLICY is invalid")
        return value

    @field_validator("rate_sync_cron")
    @classmethod
    def valid_rate_sync_cron(cls, value: str) -> str:
        schedule_parts(value)
        return value

    @field_validator("rate_sync_allowed_hosts")
    @classmethod
    def valid_rate_hosts(cls, value: str) -> str:
        hosts = {item.strip().lower() for item in value.split(",") if item.strip()}
        if not hosts or not hosts.issubset({"sce.com", "www.sce.com"}):
            raise ValueError("RATE_SYNC_ALLOWED_HOSTS may contain only approved SCE hosts")
        return ",".join(sorted(hosts))

    @field_validator("rate_sync_max_redirects")
    @classmethod
    def valid_rate_redirect_limit(cls, value: int) -> int:
        if not 0 <= value <= 5:
            raise ValueError("RATE_SYNC_MAX_REDIRECTS must be between 0 and 5")
        return value

    @field_validator("rate_sync_max_retries")
    @classmethod
    def valid_rate_retry_limit(cls, value: int) -> int:
        if not 1 <= value <= 5:
            raise ValueError("RATE_SYNC_MAX_RETRIES must be between 1 and 5")
        return value

    @field_validator("rate_sync_auto_max_percent_change")
    @classmethod
    def valid_rate_change_threshold(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("RATE_SYNC_AUTO_MAX_PERCENT_CHANGE must be between 0 and 100")
        return value

    @field_validator("rate_sync_retroactive_auto_days")
    @classmethod
    def valid_retroactive_days(cls, value: int) -> int:
        if not 0 <= value <= 31:
            raise ValueError("RATE_SYNC_RETROACTIVE_AUTO_DAYS must be between 0 and 31")
        return value

    @field_validator("rate_sync_max_source_bytes")
    @classmethod
    def valid_rate_source_limit(cls, value: int) -> int:
        if not 65_536 <= value <= 50 * 1024 * 1024:
            raise ValueError("RATE_SYNC_MAX_SOURCE_BYTES must be between 64 KiB and 50 MiB")
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
