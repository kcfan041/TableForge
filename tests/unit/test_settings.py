from __future__ import annotations

from pathlib import Path

import pytest

from tableforge.settings import SettingsError, load_settings


def test_defaults_without_file_or_environment(tmp_path: Path) -> None:
    settings = load_settings(tmp_path, environ={})

    assert settings.metadata.backend == "sqlite"
    assert settings.database_path == tmp_path / "tableforge.db"
    assert settings.worker.lease_seconds == 300.0


def test_precedence_file_then_environment_then_overrides(tmp_path: Path) -> None:
    (tmp_path / "tableforge.toml").write_text(
        "[worker]\nlease_seconds = 60\nidle_seconds = 5\ncache_max_bytes = 1000\n",
        encoding="utf-8",
    )

    settings = load_settings(
        tmp_path,
        environ={"TABLEFORGE_WORKER__IDLE_SECONDS": "2", "UNRELATED": "x"},
        overrides={"worker.lease_seconds": 30.0, "worker.cache_max_bytes": None},
    )

    assert settings.worker.lease_seconds == 30.0
    assert settings.worker.idle_seconds == 2.0
    assert settings.worker.cache_max_bytes == 1000


def test_unknown_keys_and_bad_values_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="unknown setting"):
        load_settings(tmp_path, environ={"TABLEFORGE_WORKER__TYPO": "1"})
    with pytest.raises(SettingsError, match="invalid value"):
        load_settings(tmp_path, environ={"TABLEFORGE_WORKER__LEASE_SECONDS": "soon"})
    with pytest.raises(SettingsError, match="requires metadata.url"):
        load_settings(tmp_path, environ={"TABLEFORGE_METADATA__BACKEND": "postgres"})


def test_explicit_missing_config_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="not found"):
        load_settings(tmp_path, config_file=tmp_path / "missing.toml", environ={})


def test_redaction_hides_secrets(tmp_path: Path) -> None:
    settings = load_settings(
        tmp_path,
        environ={
            "TABLEFORGE_METADATA__BACKEND": "postgres",
            "TABLEFORGE_METADATA__URL": "postgresql://forge:hunter2@db:5432/tf",
            "TABLEFORGE_ARTIFACTS__SECRET_KEY": "s3cr3t",
        },
    )

    redacted = str(settings.redacted())

    assert "hunter2" not in redacted
    assert "s3cr3t" not in redacted
    assert "postgresql://forge:***@db:5432/tf" in redacted
