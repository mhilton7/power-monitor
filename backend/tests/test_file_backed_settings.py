from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _secret(tmp_path: Path, name: str, value: str) -> Path:
    path = tmp_path / name
    path.write_text(value, encoding="utf-8")
    return path


def test_secret_files_override_direct_environment_values(tmp_path: Path) -> None:
    settings = Settings(
        database_url="postgresql+asyncpg://ignored",
        database_url_file=_secret(
            tmp_path,
            "database_url",
            "postgresql+asyncpg://power_monitor:file-secret@postgres/power_monitor\n",
        ),
        app_master_key="ignored",
        app_master_key_file=_secret(tmp_path, "app_master_key", "file-master-key\n"),
        session_pepper="ignored",
        session_pepper_file=_secret(tmp_path, "session_pepper", "file-session-pepper\n"),
        bootstrap_secret="ignored",
        bootstrap_secret_file=_secret(tmp_path, "bootstrap_secret", "file-setup-token\n"),
    )

    assert settings.database_url.endswith("file-secret@postgres/power_monitor")
    assert settings.app_master_key == "file-master-key"
    assert settings.session_pepper == "file-session-pepper"
    assert settings.bootstrap_secret == "file-setup-token"
    assert settings.production_secrets_valid


@pytest.mark.parametrize("payload", ["", "line-one\nline-two\n", "contains\x00nul"])
def test_invalid_secret_file_is_rejected(tmp_path: Path, payload: str) -> None:
    with pytest.raises(ValidationError, match="secret file"):
        Settings(app_master_key_file=_secret(tmp_path, "invalid", payload))


def test_missing_secret_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="not readable"):
        Settings(session_pepper_file=tmp_path / "missing")
