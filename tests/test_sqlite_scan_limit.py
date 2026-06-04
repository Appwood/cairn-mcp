"""Regression tests for the SQLite scan-limit window.

build_select used to emit no ORDER BY, so SQLite returned the first
``scan_limit`` rows by rowid (insertion order) and the Python-side sort ran on
that arbitrary prefix. For a newest-first search on a table larger than the
scan limit, that returned the OLDEST rows and pagination could not reach the
newest. The fix orders by rowid in the query direction before the cut.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from cairn_mcp.backends import SQLiteBackend
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import LogQuery, SearchFilters, SortOrder


def _make_db(path: Path, rows: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE logs (created_at TEXT NOT NULL, severity TEXT NOT NULL, "
            "body TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO logs VALUES (?, ?, ?)",
            [(f"2026-05-30T07:0{i}:00Z", "INFO", f"row-{i}") for i in range(rows)],
        )


def _backend(db_path: Path, scan_limit: int) -> SQLiteBackend:
    return SQLiteBackend(
        "db",
        SourceConfig(
            type="sqlite",
            path=str(db_path),
            table="logs",
            scan_limit=scan_limit,
            field_map={
                "timestamp": "created_at",
                "level": "severity",
                "message": "body",
            },
        ),
    )


def test_scan_limit_keeps_newest_rows_for_desc_search(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _make_db(db_path, rows=6)  # rowids 1..6 -> row-0..row-5, ascending time

    result = _backend(db_path, scan_limit=3).fetch(
        LogQuery(filters=SearchFilters(), order=SortOrder.DESC, limit=2)
    )

    # newest two, NOT the oldest (the pre-fix bug returned ["row-2", "row-1"])
    assert [entry.message for entry in result.entries] == ["row-5", "row-4"]
    assert any("scan limit" in warning for warning in result.warnings)


def test_scan_limit_keeps_earliest_rows_for_asc_correlation(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _make_db(db_path, rows=6)

    result = _backend(db_path, scan_limit=3).fetch(
        LogQuery(filters=SearchFilters(), order=SortOrder.ASC, limit=2)
    )

    # oldest-first timelines keep the START of the window
    assert [entry.message for entry in result.entries] == ["row-0", "row-1"]


def test_small_table_under_scan_limit_is_unaffected(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _make_db(db_path, rows=3)

    result = _backend(db_path, scan_limit=50).fetch(
        LogQuery(filters=SearchFilters(), order=SortOrder.DESC, limit=10)
    )

    assert [entry.message for entry in result.entries] == ["row-2", "row-1", "row-0"]
    assert result.warnings == []


def test_out_of_order_truncation_is_always_flagged(tmp_path: Path) -> None:
    # rowid is a proxy for insertion order; for out-of-chronological-order inserts
    # a result that hits scan_limit can miss the newest-by-timestamp row. That is
    # a documented limitation — the contract is that such an incomplete window is
    # ALWAYS flagged with a warning, never returned silently.
    db_path = tmp_path / "logs.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE logs (created_at TEXT NOT NULL, severity TEXT NOT NULL, "
            "body TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO logs VALUES (?, ?, ?)",
            [
                ("2026-05-30T07:09:00Z", "INFO", "newest_inserted_first"),  # rowid 1
                ("2026-05-30T07:00:00Z", "INFO", "a"),
                ("2026-05-30T07:01:00Z", "INFO", "b"),
                ("2026-05-30T07:02:00Z", "INFO", "c"),
            ],
        )

    result = _backend(db_path, scan_limit=2).fetch(
        LogQuery(filters=SearchFilters(), order=SortOrder.DESC, limit=2)
    )

    assert any("scan limit" in warning for warning in result.warnings)
