from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

from cairn_mcp.backends.base import BackendResult, LogBackend
from cairn_mcp.backends.registry import register_backend
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import (
    EQUALITY_FIELDS,
    LogEntry,
    LogQuery,
    SearchFilters,
    SortOrder,
    cursor_after,
    entry_matches,
    entry_sort_key,
    normalize_record,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_SCAN_LIMIT = 50_000


def _source_column(field_map: dict[str, str], field: str) -> str:
    column = field_map.get(field, field)
    if not _IDENTIFIER_RE.fullmatch(column):
        raise ValueError(f"invalid column name for field {field!r}: {column!r}")
    return column


def build_select(
    table: str,
    field_map: dict[str, str],
    filters: SearchFilters,
    *,
    scan_limit: int,
) -> tuple[str, list[object]]:
    """Build a parameterized SELECT that pushes equality filters to SQLite.

    Time ordering and the keyset cursor are applied in Python afterwards because
    the mapped timestamp column's storage format is unknown, so only the
    selective equality predicates (ids, service, logger) are pushed here. One
    extra row over ``scan_limit`` is fetched so the caller can warn on overflow.
    """
    where: list[str] = []
    params: list[object] = []
    for field in EQUALITY_FIELDS:
        value = getattr(filters, field)
        if value is not None:
            column = _source_column(field_map, field)
            where.append(f'"{column}" = ?')
            params.append(value)
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    sql = f'SELECT rowid AS "__rowid__", * FROM "{table}"{clause} LIMIT ?'
    params.append(scan_limit + 1)
    return sql, params


@register_backend
class SQLiteBackend(LogBackend):
    type_name = "sqlite"

    def __init__(self, name, config) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, config)
        self._scan_limit = config.scan_limit or DEFAULT_SCAN_LIMIT

    @classmethod
    def validate_config(cls, name: str, config: SourceConfig) -> None:
        if not (config.path or config.dsn):
            raise ValueError(f"sqlite source {name!r} requires path or dsn")
        if not config.table:
            raise ValueError(f"sqlite source {name!r} requires table")
        if not _IDENTIFIER_RE.fullmatch(config.table):
            raise ValueError(f"invalid SQLite table name for source {name!r}")

    def fetch(self, query: LogQuery) -> BackendResult:
        result = BackendResult()
        matched: list[LogEntry] = []
        for entry in self._entries(query.filters, result):
            if not entry_matches(entry, query.filters, include_text=False):
                continue
            if query.cursor and not cursor_after(entry, query.cursor, query.order):
                continue
            matched.append(entry)
        matched.sort(key=entry_sort_key, reverse=query.order == SortOrder.DESC)
        result.entries = matched[: query.limit]
        return result

    def get_by_id(self, log_id: str) -> BackendResult:
        result = BackendResult()
        for entry in self._entries(SearchFilters(), result):
            if entry.id == log_id:
                result.entries.append(entry)
                break
        return result

    def aggregate(self, filters: SearchFilters, *, time_bucket: str):
        from cairn_mcp.backends.base import Aggregates
        from cairn_mcp.schema import bucket_timestamp

        result = BackendResult()
        aggregates = Aggregates()
        for entry in self._entries(filters, result):
            if not entry_matches(entry, filters, include_text=False):
                continue
            aggregates.total += 1
            aggregates.by_level[entry.level] += 1
            aggregates.by_service[entry.service or "unknown"] += 1
            aggregates.by_logger[entry.logger or "unknown"] += 1
            aggregates.by_time_bucket[bucket_timestamp(entry.timestamp, time_bucket)] += 1
        aggregates.warnings.extend(result.warnings)
        return aggregates

    def _entries(
        self, filters: SearchFilters, result: BackendResult
    ) -> Iterator[LogEntry]:
        for rowid, row in self._rows(filters, result):
            fallback_id = f"{self.name}:{rowid}" if rowid is not None else self.name
            try:
                yield normalize_record(
                    row,
                    source_name=self.name,
                    field_map=self.config.field_map,
                    fallback_id=fallback_id,
                )
            except ValueError as exc:
                # value-free: exc text can echo the raw (possibly secret) column value
                result.warnings.append(
                    f"{self.name}:{rowid}: skipped malformed record ({type(exc).__name__})"
                )

    def _rows(
        self, filters: SearchFilters, result: BackendResult
    ) -> Iterator[tuple[object, dict[str, object]]]:
        try:
            sql, params = build_select(
                self.config.table or "",
                self.config.field_map,
                filters,
                scan_limit=self._scan_limit,
            )
        except ValueError as exc:
            result.warnings.append(f"{self.name}: {exc}")
            return
        try:
            with self._connect() as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only = ON")
                cursor = connection.execute(sql, params)
                fetched = cursor.fetchall()
        except (sqlite3.Error, ValueError) as exc:
            result.warnings.append(f"{self.name}: SQLite read failed: {exc}")
            return
        if len(fetched) > self._scan_limit:
            result.warnings.append(
                f"{self.name}: scan limit {self._scan_limit} reached; "
                "add more selective filters or raise scan_limit — results may be incomplete"
            )
            fetched = fetched[: self._scan_limit]
        for row in fetched:
            mapping = dict(row)
            yield mapping.pop("__rowid__", None), mapping

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._read_only_uri(), uri=True)

    def _read_only_uri(self) -> str:
        if self.config.path:
            path = Path(self.config.path).resolve()
            return f"file:{quote(path.as_posix())}?mode=ro"
        dsn = self.config.dsn or ""
        if dsn.startswith("file:"):
            if re.search(r"([?&])mode=ro(&|$)", dsn):
                return dsn
            if re.search(r"([?&])mode=", dsn):
                raise ValueError("SQLite dsn must use mode=ro or omit mode")
            separator = "&" if "?" in dsn else "?"
            return f"{dsn}{separator}mode=ro"
        path = Path(dsn).resolve()
        return f"file:{quote(path.as_posix())}?mode=ro"
