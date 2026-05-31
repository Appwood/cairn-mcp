from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cairn_mcp.backends import SQLiteBackend
from cairn_mcp.backends.sqlite import build_select
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import LogQuery, SearchFilters


def _create_db(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE logs (
                created_at TEXT NOT NULL,
                severity TEXT NOT NULL,
                body TEXT NOT NULL,
                request_id TEXT,
                attributes TEXT
            )
            """
        )
        connection.executemany(
            "INSERT INTO logs VALUES (?, ?, ?, ?, ?)",
            [
                ("2026-05-30T07:00:00Z", "INFO", "start", "req-1", '{"attempt": 1}'),
                ("2026-05-30T07:01:00Z", "ERROR", "boom", "req-1", '{"attempt": 2}'),
            ],
        )


def _backend(db_path: Path) -> SQLiteBackend:
    return SQLiteBackend(
        "db",
        SourceConfig(
            type="sqlite",
            path=str(db_path),
            table="logs",
            field_map={
                "timestamp": "created_at",
                "level": "severity",
                "message": "body",
            },
        ),
    )


def test_sqlite_backend_maps_columns_filters_and_derives_row_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _create_db(db_path)

    result = _backend(db_path).fetch(LogQuery(filters=SearchFilters(levels="ERROR")))

    assert [entry.id for entry in result.entries] == ["db:2"]
    assert result.entries[0].attributes["attempt"] == 2


def test_build_select_pushes_equality_filters_parameterized() -> None:
    sql, params = build_select(
        "logs",
        {"request_id": "request_id"},
        SearchFilters(request_id="req-1", job_id="job-9"),
        scan_limit=10,
    )

    assert '"request_id" = ?' in sql
    assert '"job_id" = ?' in sql
    assert sql.strip().endswith("LIMIT ?")
    # values are parameters, never interpolated; last param is scan_limit + 1
    assert params == ["req-1", "job-9", 11]


def test_build_select_rejects_unsafe_column_names() -> None:
    with pytest.raises(ValueError):
        build_select(
            "logs",
            {"request_id": "req; DROP TABLE logs"},
            SearchFilters(request_id="x"),
            scan_limit=10,
        )


def test_sqlite_connection_is_read_only(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _create_db(db_path)
    backend = SQLiteBackend(
        "db", SourceConfig(type="sqlite", path=str(db_path), table="logs")
    )

    with pytest.raises(sqlite3.OperationalError):
        with backend._connect() as connection:
            connection.execute("CREATE TABLE should_fail (id INTEGER)")


def test_sqlite_backend_rejects_write_mode_dsn(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _create_db(db_path)
    backend = SQLiteBackend(
        "db",
        SourceConfig(type="sqlite", dsn=f"file:{db_path}?mode=rw", table="logs"),
    )

    result = backend.fetch(LogQuery(filters=SearchFilters()))

    assert result.entries == []
    assert any("mode=ro" in warning for warning in result.warnings)
