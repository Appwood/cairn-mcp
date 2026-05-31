from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


CONFIG_ENV_VAR = "CAIRN_CONFIG"
DEFAULT_TIME_WINDOW = "24h"
DEFAULT_MAX_RESULTS = 100
HARD_MAX_RESULTS = 500
DEFAULT_MAX_SCAN = 5_000

# Always-on sensitive field names. User config adds to this set; it cannot
# silently shrink it (see RedactionConfig.replace_default_fields).
DEFAULT_SENSITIVE_FIELDS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "session_token",
    "api_key",
    "apikey",
    "authorization",
    "credentials",
    "private_key",
    "client_secret",
)


class ConfigError(ValueError):
    """Raised when adapter configuration cannot be loaded."""


class RedactionPatternConfig(BaseModel):
    name: str
    pattern: str
    replacement: str = "[REDACTED]"

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid redaction pattern {value!r}: {exc}") from exc
        return value


class RedactionConfig(BaseModel):
    # Empty by default: configured fields ADD to DEFAULT_SENSITIVE_FIELDS rather
    # than replace them, so customizing redaction can never weaken the baseline.
    fields: list[str] = Field(default_factory=list)
    patterns: list[RedactionPatternConfig] = Field(default_factory=list)
    redact_emails: bool = False
    replace_default_fields: bool = False

    @field_validator("patterns", mode="before")
    @classmethod
    def _normalize_patterns(cls, value: Any) -> Any:
        if value in (None, ""):
            return []
        normalized = []
        for index, item in enumerate(value):
            if isinstance(item, str):
                normalized.append({"name": f"custom_{index + 1}", "pattern": item})
            else:
                normalized.append(item)
        return normalized

    def effective_fields(self) -> set[str]:
        configured = {field.lower() for field in self.fields}
        if self.replace_default_fields:
            return configured
        return {field.lower() for field in DEFAULT_SENSITIVE_FIELDS} | configured


class SourceConfig(BaseModel):
    # Free-form: resolved against the backend registry at startup, so
    # third-party backends registered via entry points are accepted too.
    type: str
    path: str | None = None
    dsn: str | None = None
    table: str | None = None
    field_map: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    scan_limit: int | None = None

    @field_validator("type")
    @classmethod
    def _normalize_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("source type is required")
        return normalized

    @field_validator("scan_limit")
    @classmethod
    def _validate_scan_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("scan_limit must be positive")
        return value


class AdapterConfig(BaseModel):
    sources: dict[str, SourceConfig]
    redaction: RedactionConfig = Field(default_factory=RedactionConfig)
    default_time_window: str = DEFAULT_TIME_WINDOW
    max_results: int = DEFAULT_MAX_RESULTS
    max_scan: int = DEFAULT_MAX_SCAN

    @field_validator("max_results")
    @classmethod
    def _validate_max_results(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_results must be positive")
        return min(value, HARD_MAX_RESULTS)

    @field_validator("max_scan")
    @classmethod
    def _validate_max_scan(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_scan must be positive")
        return value

    @field_validator("default_time_window")
    @classmethod
    def _validate_window(cls, value: str) -> str:
        parse_duration(value)  # raises ConfigError on bad input, fail at load time
        return value

    @model_validator(mode="after")
    def _validate_sources(self) -> "AdapterConfig":
        if not self.sources:
            raise ValueError("at least one source is required")
        return self


def load_config(path: str | os.PathLike[str] | None = None) -> AdapterConfig:
    raw_path = path or os.environ.get(CONFIG_ENV_VAR, "")
    if not str(raw_path).strip():
        raise ConfigError(
            f"configuration path is required; pass --config or set {CONFIG_ENV_VAR}"
        )
    config_path = Path(raw_path)
    if not config_path.exists():
        raise ConfigError(f"configuration file does not exist: {config_path}")
    if config_path.is_dir():
        raise ConfigError(f"configuration path is a directory, not a file: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    loaded = _expand_source_env(loaded)
    loaded = _resolve_relative_source_paths(loaded, config_path.parent)

    try:
        return AdapterConfig.model_validate(loaded)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value)
    if not match:
        raise ConfigError(
            "duration must use a number plus unit: s, m, h, d, or w; for example 24h"
        )
    amount = int(match.group(1))
    unit = match.group(2)
    match unit:
        case "s":
            return timedelta(seconds=amount)
        case "m":
            return timedelta(minutes=amount)
        case "h":
            return timedelta(hours=amount)
        case "d":
            return timedelta(days=amount)
        case "w":
            return timedelta(weeks=amount)
    raise ConfigError(f"unsupported duration unit: {unit}")


def _expand_source_env(data: Any) -> Any:
    """Expand ${ENV} only in source ``path`` and ``dsn`` — the only fields meant
    to reference the environment. Expanding everywhere risks pulling a process
    secret into a field (e.g. ``type``) that is later echoed in an error.
    """
    if not isinstance(data, dict):
        return data
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return data
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        for key in ("path", "dsn"):
            value = source.get(key)
            if isinstance(value, str):
                source[key] = os.path.expandvars(value)
    return data


def _resolve_relative_source_paths(data: Any, base_dir: Path) -> Any:
    if not isinstance(data, dict):
        return data
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return data
    for source in sources.values():
        if not isinstance(source, dict):
            continue
        path = source.get("path")
        if isinstance(path, str) and path and not Path(path).is_absolute():
            source["path"] = str((base_dir / path).resolve())
    return data
