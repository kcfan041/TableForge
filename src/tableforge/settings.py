from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

ENV_PREFIX = "TABLEFORGE_"
CONFIG_FILE = "tableforge.toml"


class SettingsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataSettings:
    backend: str = "sqlite"
    url: str = ""


@dataclass(frozen=True, slots=True)
class ArtifactSettings:
    backend: str = "local"
    bucket: str = "tableforge"
    endpoint_url: str = ""
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""
    presign_seconds: int = 900


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    lease_seconds: float = 300.0
    idle_seconds: float = 1.0
    cache_max_bytes: int = 10 * 1024**3
    use_gpu: bool = True


@dataclass(frozen=True, slots=True)
class CoordinatorSettings:
    listen: str = "0.0.0.0:7443"
    reconcile_seconds: float = 2.0
    metrics_listen: str = ""
    node_ttl_seconds: float = 120.0


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path = Path(".tableforge")
    metadata: MetadataSettings = field(default_factory=MetadataSettings)
    artifacts: ArtifactSettings = field(default_factory=ArtifactSettings)
    worker: WorkerSettings = field(default_factory=WorkerSettings)
    coordinator: CoordinatorSettings = field(default_factory=CoordinatorSettings)

    def __post_init__(self) -> None:
        if self.metadata.backend not in ("sqlite", "postgres"):
            raise SettingsError(f"unknown metadata backend: {self.metadata.backend}")
        if self.metadata.backend == "postgres" and not self.metadata.url:
            raise SettingsError("postgres metadata backend requires metadata.url")
        if self.artifacts.backend not in ("local", "s3"):
            raise SettingsError(f"unknown artifact backend: {self.artifacts.backend}")
        if self.worker.lease_seconds <= 0 or self.worker.idle_seconds < 0:
            raise SettingsError("worker timing values must be positive")
        if self.worker.cache_max_bytes < 0:
            raise SettingsError("worker.cache_max_bytes cannot be negative")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tableforge.db"

    def redacted(self) -> dict[str, object]:
        data = _to_dict(self)
        artifacts = data["artifacts"]
        assert isinstance(artifacts, dict)
        for key in ("access_key", "secret_key"):
            if artifacts.get(key):
                artifacts[key] = "***"
        metadata = data["metadata"]
        assert isinstance(metadata, dict)
        if metadata.get("url"):
            metadata["url"] = _redact_url(str(metadata["url"]))
        return data


def load_settings(
    data_dir: Path,
    config_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> Settings:
    environment = os.environ if environ is None else environ
    settings = Settings(data_dir=data_dir)
    path = config_file or data_dir / CONFIG_FILE
    if config_file is not None and not path.exists():
        raise SettingsError(f"config file not found: {path}")
    if path.exists():
        try:
            document = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as error:
            raise SettingsError(f"invalid config file {path}: {error}") from error
        settings = _apply(settings, _flatten(document), source=str(path))
    env_values = {
        key.removeprefix(ENV_PREFIX).lower().replace("__", "."): value
        for key, value in environment.items()
        if key.startswith(ENV_PREFIX)
    }
    settings = _apply(settings, env_values, source="environment")
    if overrides:
        settings = _apply(
            settings,
            {key: value for key, value in overrides.items() if value is not None},
            source="command line",
        )
    return settings


_SECTIONS = ("metadata", "artifacts", "worker", "coordinator")


def _apply(settings: Settings, values: Mapping[str, object], source: str) -> Settings:
    sections: dict[str, dict[str, object]] = {name: {} for name in _SECTIONS}
    top: dict[str, object] = {}
    for dotted, value in values.items():
        section, _, name = dotted.partition(".")
        if not name:
            if section != "data_dir":
                raise SettingsError(f"unknown setting '{dotted}' from {source}")
            top["data_dir"] = Path(str(value))
            continue
        if section not in sections:
            raise SettingsError(f"unknown setting '{dotted}' from {source}")
        sections[section][name] = value
    updates: dict[str, Any] = dict(top)
    for section, entries in sections.items():
        if not entries:
            continue
        current = getattr(settings, section)
        known = {item.name: item.type for item in fields(current)}
        converted: dict[str, object] = {}
        for name, value in entries.items():
            if name not in known:
                raise SettingsError(f"unknown setting '{section}.{name}' from {source}")
            converted[name] = _convert(value, getattr(current, name), f"{section}.{name}")
        updates[section] = replace(current, **converted)
    return replace(settings, **updates)


def _convert(value: object, current: object, name: str) -> object:
    try:
        if isinstance(current, bool):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if isinstance(current, int):
            return int(str(value))
        if isinstance(current, float):
            return float(str(value))
    except ValueError as error:
        raise SettingsError(f"invalid value for {name}: {value!r}") from error
    return str(value)


def _flatten(document: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    flat: dict[str, object] = {}
    for key, value in document.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _to_dict(value: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in fields(value):  # type: ignore[arg-type]
        attribute = getattr(value, item.name)
        if hasattr(attribute, "__dataclass_fields__"):
            result[item.name] = _to_dict(attribute)
        elif isinstance(attribute, Path):
            result[item.name] = str(attribute)
        else:
            result[item.name] = attribute
    return result


def _redact_url(url: str) -> str:
    scheme, separator, rest = url.partition("://")
    if not separator or "@" not in rest:
        return url
    credentials, _, host = rest.rpartition("@")
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"
