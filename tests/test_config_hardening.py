"""Regression tests for config load-time robustness.

Covers a duration that overflowed timedelta into an uncaught OverflowError,
a zero-length window that silently matched nothing, and an undefined
environment variable that was carried into a path/dsn literally.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cairn_mcp.config import ConfigError, load_config, parse_duration


def test_zero_duration_window_is_rejected() -> None:
    for value in ("0h", "0s", "0m"):
        with pytest.raises(ConfigError):
            parse_duration(value)


def test_overflowing_duration_raises_config_error_not_overflow() -> None:
    # Previously raised a raw OverflowError that escaped load_config's
    # ValidationError handler and crashed the server.
    with pytest.raises(ConfigError):
        parse_duration("99999999999999999999w")


def test_overflowing_window_in_config_is_a_clean_config_error(tmp_path: Path) -> None:
    (tmp_path / "logs.jsonl").write_text("", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
default_time_window: 99999999999999999999w
sources:
  app:
    type: jsonl
    path: logs.jsonl
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        load_config(config_path)


def test_unresolved_env_var_in_path_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("DEFINITELY_UNSET_VAR", raising=False)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sources:
  app:
    type: jsonl
    path: ${DEFINITELY_UNSET_VAR}/logs.jsonl
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as exc:
        load_config(config_path)
    assert "unresolved environment variable" in str(exc.value)
