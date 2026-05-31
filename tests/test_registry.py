from __future__ import annotations

import pytest

from cairn_mcp.backends import (
    JSONLBackend,
    PostgresBackend,
    SQLiteBackend,
    available_backends,
    get_backend_class,
    register_backend,
)
from cairn_mcp.backends.base import BackendResult, LogBackend
from cairn_mcp.config import ConfigError


def test_builtin_backends_are_registered() -> None:
    assert get_backend_class("jsonl") is JSONLBackend
    assert get_backend_class("sqlite") is SQLiteBackend
    assert get_backend_class("postgres") is PostgresBackend
    assert {"jsonl", "sqlite", "postgres"} <= set(available_backends())


def test_unknown_type_lists_available_backends() -> None:
    with pytest.raises(ConfigError) as exc:
        get_backend_class("loki")
    assert "loki" in str(exc.value)
    assert "jsonl" in str(exc.value)


def test_register_backend_is_pluggable() -> None:
    class MemoryBackend(LogBackend):
        type_name = "memory_test"

        def fetch(self, query):  # type: ignore[no-untyped-def]
            return BackendResult()

        def get_by_id(self, log_id):  # type: ignore[no-untyped-def]
            return BackendResult()

    register_backend(MemoryBackend)
    assert get_backend_class("memory_test") is MemoryBackend


def test_register_backend_requires_type_name() -> None:
    class Nameless(LogBackend):
        def fetch(self, query):  # type: ignore[no-untyped-def]
            return BackendResult()

        def get_by_id(self, log_id):  # type: ignore[no-untyped-def]
            return BackendResult()

    with pytest.raises(ValueError):
        register_backend(Nameless)
