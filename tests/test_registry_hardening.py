"""Regression tests for backend registry robustness.

A duplicate registration (a plugin claiming a built-in's type name) used to
silently replace the built-in, and entry-point load failures were collected
into a warnings list that every caller discarded. Both are now surfaced.
"""

from __future__ import annotations

import logging

from cairn_mcp.backends import (
    SQLiteBackend,
    get_backend_class,
    register_backend,
)
from cairn_mcp.backends import registry as reg
from cairn_mcp.backends.base import BackendResult, LogBackend


def test_duplicate_registration_does_not_override_builtin(caplog) -> None:
    class FakeSqlite(LogBackend):
        type_name = "sqlite"

        def fetch(self, query):  # type: ignore[no-untyped-def]
            return BackendResult()

        def get_by_id(self, log_id):  # type: ignore[no-untyped-def]
            return BackendResult()

    with caplog.at_level(logging.WARNING):
        register_backend(FakeSqlite)

    # the built-in is preserved; the shadowing class is ignored + logged
    assert get_backend_class("sqlite") is SQLiteBackend
    assert any("sqlite" in record.getMessage() for record in caplog.records)


def test_failing_entry_point_plugin_is_logged(monkeypatch, caplog) -> None:
    class _BadEntryPoint:
        name = "loki"

        def load(self):
            raise ImportError("missing dependency")

    monkeypatch.setattr(reg, "_ENTRY_POINTS_LOADED", False)
    monkeypatch.setattr(
        reg.metadata, "entry_points", lambda group=None: [_BadEntryPoint()]
    )

    with caplog.at_level(logging.WARNING):
        warnings = reg._load_entry_point_backends()

    assert any("loki" in warning for warning in warnings)
    assert any("loki" in record.getMessage() for record in caplog.records)
