from __future__ import annotations

from pathlib import Path

import pytest

from cairn_mcp.config import (
    CONFIG_ENV_VAR,
    HARD_MAX_RESULTS,
    ConfigError,
    load_config,
    parse_duration,
)


def test_load_config_from_env_and_expand_values(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "logs.jsonl"
    log_path.write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
default_time_window: 15m
max_results: 999
sources:
  app:
    type: jsonl
    path: ${LOG_PATH}
    field_map:
      timestamp: ts
      level: severity
      message: msg
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LOG_PATH", str(log_path))
    monkeypatch.setenv(CONFIG_ENV_VAR, str(config_path))

    config = load_config()

    assert config.default_time_window == "15m"
    assert config.max_results == HARD_MAX_RESULTS
    assert config.sources["app"].path == str(log_path)


def test_resolves_relative_source_paths_from_config_file(tmp_path: Path) -> None:
    (tmp_path / "logs.jsonl").write_text("", encoding="utf-8")
    config_path = tmp_path / "nested.yaml"
    config_path.write_text(
        """
sources:
  app:
    type: jsonl
    path: logs.jsonl
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.sources["app"].path == str((tmp_path / "logs.jsonl").resolve())


def test_parse_duration_accepts_expected_units() -> None:
    assert parse_duration("30s").total_seconds() == 30
    assert parse_duration("2m").total_seconds() == 120
    assert parse_duration("3h").total_seconds() == 10800
    assert parse_duration("4d").days == 4
    assert parse_duration("1w").days == 7


def test_missing_config_path_raises_clear_error(monkeypatch) -> None:
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    with pytest.raises(ConfigError) as exc:
        load_config()
    assert "configuration path is required" in str(exc.value)


def test_invalid_redaction_pattern_is_rejected_at_load(tmp_path: Path) -> None:
    (tmp_path / "logs.jsonl").write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
redaction:
  patterns:
    - name: broken
      pattern: "(unclosed"
sources:
  app:
    type: jsonl
    path: logs.jsonl
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_unknown_source_type_is_rejected_with_available_list(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sources:
  app:
    type: loki
    path: logs.jsonl
""",
        encoding="utf-8",
    )

    # validated lazily when backends are built
    from cairn_mcp.tools import LogAdapter

    config = load_config(config_path)
    with pytest.raises(ConfigError) as exc:
        LogAdapter(config)
    assert "loki" in str(exc.value)


def test_env_expansion_is_limited_to_path_and_dsn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LEAKME", "super-secret-value")
    (tmp_path / "logs.jsonl").write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sources:
  app:
    type: ${LEAKME}
    path: logs.jsonl
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    # 'type' is NOT env-expanded -> the secret never enters the value...
    assert config.sources["app"].type == "${leakme}"

    # ...and therefore can't surface in the unknown-backend error.
    from cairn_mcp.tools import LogAdapter

    with pytest.raises(ConfigError) as exc:
        LogAdapter(config)
    assert "super-secret-value" not in str(exc.value)
