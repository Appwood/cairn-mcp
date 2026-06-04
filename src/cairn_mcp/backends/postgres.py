from __future__ import annotations

import atexit
import re
from typing import Any

from cairn_mcp.backends.base import Aggregates, BackendResult, LogBackend
from cairn_mcp.backends.registry import register_backend
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import (
    EQUALITY_FIELDS,
    Cursor,
    LogQuery,
    SearchFilters,
    SortOrder,
    normalize_record,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Cap the rows pulled for the high-cardinality stats dimensions (service,
# logger). Without a bound a table with millions of distinct values would stream
# one GROUP BY row per value into memory; with it we keep the top-N by count —
# what a stats overview wants — and warn when the cut bites. The bounded-
# cardinality dimensions (level, time bucket) are never capped.
MAX_STATS_GROUPS = 100
_CAPPED_DIMENSIONS = ("by_service", "by_logger")


def _ident(name: str, *, what: str = "identifier") -> str:
    if not _IDENTIFIER_RE.fullmatch(name):
        raise ValueError(f"invalid {what}: {name!r}")
    return f'"{name}"'


def _column(field_map: dict[str, str], field: str) -> str:
    return _ident(field_map.get(field, field), what=f"column for {field}")


def _qualified_table(table: str, schema: str | None) -> str:
    table_ref = _ident(table, what="table")
    if schema:
        return f"{_ident(schema, what='schema')}.{table_ref}"
    return table_ref


def _direction(order: SortOrder) -> str:
    return "DESC" if order == SortOrder.DESC else "ASC"


def _where(
    field_map: dict[str, str],
    filters: SearchFilters,
    cursor: Cursor | None,
    order: SortOrder,
    source_name: str = "",
) -> tuple[list[str], list[Any]]:
    ts_col = _column(field_map, "timestamp")
    id_col = _column(field_map, "id")
    clauses: list[str] = []
    params: list[Any] = []

    if filters.levels:
        level_col = _column(field_map, "level")
        clauses.append(f"upper({level_col}::text) = ANY(%s)")
        params.append(sorted(level.upper() for level in filters.levels))
    if filters.start_time is not None:
        clauses.append(f"{ts_col} >= %s")
        params.append(filters.start_time)
    if filters.end_time is not None:
        clauses.append(f"{ts_col} <= %s")
        params.append(filters.end_time)
    for field in EQUALITY_FIELDS:
        value = getattr(filters, field)
        if value is not None:
            # Compare via ::text so a non-text column (e.g. a uuid request_id)
            # doesn't raise "operator does not exist: uuid = text": psycopg sends
            # the parameter as text under server-side binding. For text columns
            # the cast is an identity and the btree index is still usable; only a
            # non-text column loses its index here (acceptable for the correlation
            # query, which is also time-bounded). Mirrors get_by_id and the keyset.
            clauses.append(f"{_column(field_map, field)}::text = %s")
            params.append(value)
    if cursor is not None:
        # Keyset pagination over the global order (timestamp, id, source). source
        # is this backend's constant name; it is the final tiebreaker so an entry
        # that shares a timestamp+id with another source isn't skipped. id and
        # source are compared as text with the C collation so the database orders
        # them byte-for-byte the same way Python str does.
        comparator = "<" if order == SortOrder.DESC else ">"
        clauses.append(
            f'({ts_col}, {id_col}::text COLLATE "C", %s::text COLLATE "C") '
            f"{comparator} (%s, %s, %s)"
        )
        params.extend([source_name, cursor.timestamp, cursor.id, cursor.source])
    return clauses, params


def build_fetch_sql(
    table: str,
    field_map: dict[str, str],
    query: LogQuery,
    *,
    schema: str | None = None,
    source_name: str = "",
) -> tuple[str, list[Any]]:
    ts_col = _column(field_map, "timestamp")
    id_col = _column(field_map, "id")
    direction = _direction(query.order)
    clauses, params = _where(
        field_map, query.filters, query.cursor, query.order, source_name
    )
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM {_qualified_table(table, schema)}{where} "
        f'ORDER BY {ts_col} {direction}, {id_col}::text COLLATE "C" {direction} '
        f"LIMIT %s"
    )
    params.append(query.limit)
    return sql, params


def build_aggregate_sql(
    table: str,
    field_map: dict[str, str],
    filters: SearchFilters,
    *,
    time_bucket: str,
    schema: str | None = None,
    available_columns: set[str] | None = None,
) -> dict[str, tuple[str, list[Any]]]:
    """Build one GROUP BY query per stats dimension, all pushed to Postgres.

    Optional dimensions (service, logger) are only emitted when their column is
    known to exist, so minimal log tables that omit them don't error. When
    ``available_columns`` is None (or empty) every dimension is emitted.
    """
    if time_bucket not in ("minute", "hour", "day"):
        raise ValueError("time_bucket must be minute, hour, or day")
    clauses, base_params = _where(field_map, filters, None, SortOrder.DESC)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    table_ref = _qualified_table(table, schema)
    ts_col = _column(field_map, "timestamp")

    def group(expr: str, *, capped: bool = False) -> tuple[str, list[Any]]:
        sql = (
            f"SELECT {expr} AS bucket, count(*) AS n "
            f"FROM {table_ref}{where} GROUP BY bucket"
        )
        params = list(base_params)
        if capped:
            # top-N by count, bounded so an unbounded-cardinality column can't
            # pull a row per distinct value into memory. Fetch one extra row as
            # an overflow sentinel so the caller can tell "exactly N groups"
            # (complete) apart from "more than N" (truncated).
            sql += " ORDER BY n DESC LIMIT %s"
            params.append(MAX_STATS_GROUPS + 1)
        return sql, params

    def has_column(field: str) -> bool:
        if not available_columns:
            return True
        return field_map.get(field, field) in available_columns

    queries = {
        "total": (
            f"SELECT count(*) AS n FROM {table_ref}{where}",
            list(base_params),
        ),
        "by_level": group(f"upper({_column(field_map, 'level')}::text)"),
        # Truncate in UTC and append the +00:00 offset so bucket keys are
        # byte-identical to the file backends' datetime.isoformat() output.
        "by_time_bucket": (
            f"SELECT to_char(date_trunc(%s, ({ts_col} AT TIME ZONE 'UTC')), "
            f"'YYYY-MM-DD\"T\"HH24:MI:SS') || '+00:00' AS bucket, count(*) AS n "
            f"FROM {table_ref}{where} GROUP BY bucket",
            [time_bucket, *base_params],
        ),
    }
    if has_column("service"):
        queries["by_service"] = group(
            f"{_column(field_map, 'service')}::text", capped=True
        )
    if has_column("logger"):
        queries["by_logger"] = group(
            f"{_column(field_map, 'logger')}::text", capped=True
        )
    return queries


@register_backend
class PostgresBackend(LogBackend):
    """Read-only Postgres source with full predicate, ordering, and aggregate
    push-down — the reference backend for production-scale log tables."""

    type_name = "postgres"

    def __init__(self, name, config) -> None:  # type: ignore[no-untyped-def]
        super().__init__(name, config)
        self._schema = config.options.get("schema")
        self._field_map = config.field_map
        self._column_cache: set[str] | None = None
        self._columns_loaded = False
        self._pool: Any = None        # lazily created psycopg_pool.ConnectionPool
        self._pool_failed = False     # driver missing / pool open failed → degrade once

    @classmethod
    def validate_config(cls, name: str, config: SourceConfig) -> None:
        if not config.dsn:
            raise ValueError(f"postgres source {name!r} requires a dsn")
        if not config.table:
            raise ValueError(f"postgres source {name!r} requires a table")
        if not _IDENTIFIER_RE.fullmatch(config.table):
            raise ValueError(f"invalid Postgres table name for source {name!r}")

    def fetch(self, query: LogQuery) -> BackendResult:
        result = BackendResult()
        try:
            sql, params = build_fetch_sql(
                self.config.table or "",
                self._field_map,
                query,
                schema=self._schema,
                source_name=self.name,
            )
        except ValueError as exc:
            result.warnings.append(f"{self.name}: {exc}")
            return result
        rows = self._execute(sql, params, result)
        id_col = self._field_map.get("id", "id")
        for row in rows:
            fallback = f"{self.name}:{row.get(id_col)}"
            try:
                result.entries.append(
                    normalize_record(
                        row,
                        source_name=self.name,
                        field_map=self._field_map,
                        fallback_id=fallback,
                    )
                )
            except ValueError as exc:
                # value-free: exc text can echo the raw (possibly secret) row value
                result.warnings.append(
                    f"{self.name}: skipped malformed record ({type(exc).__name__})"
                )
        return result

    def get_by_id(self, log_id: str) -> BackendResult:
        result = BackendResult()
        local_id = log_id.split(":", 1)[1] if log_id.startswith(f"{self.name}:") else log_id
        id_col = self._field_map.get("id", "id")
        try:
            column = _ident(id_col, what="id column")
        except ValueError as exc:
            result.warnings.append(f"{self.name}: {exc}")
            return result
        sql = (
            f"SELECT * FROM {_qualified_table(self.config.table or '', self._schema)} "
            f"WHERE {column}::text = %s LIMIT 1"
        )
        rows = self._execute(sql, [local_id], result)
        for row in rows:
            try:
                result.entries.append(
                    normalize_record(
                        row,
                        source_name=self.name,
                        field_map=self._field_map,
                        fallback_id=log_id,
                    )
                )
            except ValueError as exc:
                # value-free: exc text can echo the raw (possibly secret) row value
                result.warnings.append(
                    f"{self.name}: skipped malformed record ({type(exc).__name__})"
                )
        return result

    def aggregate(self, filters: SearchFilters, *, time_bucket: str) -> Aggregates:
        aggregates = Aggregates()
        try:
            queries = build_aggregate_sql(
                self.config.table or "",
                self._field_map,
                filters,
                time_bucket=time_bucket,
                schema=self._schema,
                available_columns=self._available_columns(_Warns(aggregates)),
            )
        except ValueError as exc:
            aggregates.warnings.append(f"{self.name}: {exc}")
            return aggregates
        total_sql, total_params = queries.pop("total")
        total_rows = self._execute(total_sql, total_params, _Warns(aggregates))
        if total_rows:
            aggregates.total = int(total_rows[0]["n"])
        dimension_targets = {
            "by_level": aggregates.by_level,
            "by_service": aggregates.by_service,
            "by_logger": aggregates.by_logger,
            "by_time_bucket": aggregates.by_time_bucket,
        }
        for key, (sql, params) in queries.items():
            counter = dimension_targets[key]
            rows = self._execute(sql, params, _Warns(aggregates))
            if key in _CAPPED_DIMENSIONS and len(rows) > MAX_STATS_GROUPS:
                # more than the cap exists (the query fetched cap+1): drop the
                # overflow sentinel and warn. Exactly-cap groups return cap rows
                # and are complete — no false warning.
                rows = rows[:MAX_STATS_GROUPS]
                aggregates.warnings.append(
                    f"{self.name}: {key} truncated to top {MAX_STATS_GROUPS} by count"
                )
            for row in rows:
                bucket = row["bucket"]
                counter[str(bucket) if bucket is not None else "unknown"] += int(row["n"])
        # A dimension whose column the table lacks still applies to every row —
        # report it as "unknown" (matching the file backends), not as empty.
        for optional in ("by_service", "by_logger"):
            if optional not in queries and aggregates.total > 0:
                dimension_targets[optional]["unknown"] = aggregates.total
        return aggregates

    def _available_columns(self, sink) -> set[str] | None:
        """Column names of the configured table, cached. None if undeterminable.

        Used to skip GROUP BY on optional columns a minimal table may not have.
        """
        if self._columns_loaded:
            return self._column_cache
        if self._schema:
            sql = (
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = %s"
            )
            params: list[Any] = [self.config.table, self._schema]
        else:
            sql = (
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s AND table_schema = current_schema()"
            )
            params = [self.config.table]
        rows = self._execute(sql, params, sink)
        columns = {row["column_name"] for row in rows}
        self._column_cache = columns or None
        self._columns_loaded = True
        return self._column_cache

    def _create_pool(self):
        """Build the connection pool. Isolated (and overridable in tests) so the
        psycopg import lives here only; everything else stays driver-free."""
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        def _configure(conn) -> None:  # type: ignore[no-untyped-def]
            # One-time per fresh connection: read-only, autocommit (each statement
            # is its own tiny transaction), and dict rows so callers index by name.
            conn.autocommit = True
            conn.read_only = True
            conn.row_factory = dict_row

        # check=check_connection runs a cheap liveness probe on borrow, so a stale
        # or server-dropped connection is detected and replaced automatically —
        # this is what makes reuse safe without hand-rolled reconnect logic.
        return ConnectionPool(
            self.config.dsn or "",
            min_size=0,
            max_size=4,
            open=True,
            configure=_configure,
            check=ConnectionPool.check_connection,
            name=f"cairn-{self.name}",
        )

    def _get_pool(self, result):
        """Return the shared pool, creating it lazily. None (with a warning) if
        the driver is missing or the pool can't open; the failure is cached so we
        don't retry — and warn — on every query."""
        if self._pool is not None:
            return self._pool
        if self._pool_failed:
            return None
        try:
            self._pool = self._create_pool()
        except ImportError:
            self._pool_failed = True
            result.warnings.append(
                f"{self.name}: postgres source requires the 'psycopg' and "
                "'psycopg-pool' packages; install with: pip install 'cairn-mcp[postgres]'"
            )
            return None
        except Exception as exc:  # noqa: BLE001 - any open failure degrades gracefully
            self._pool_failed = True
            result.warnings.append(f"{self.name}: could not open Postgres connection pool: {exc}")
            return None
        atexit.register(self._close_pool)
        return self._pool

    def _close_pool(self) -> None:
        pool = self._pool
        if pool is not None:
            self._pool = None
            try:
                pool.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup at shutdown
                pass

    def _execute(self, sql: str, params: list[Any], result) -> list[dict[str, Any]]:
        pool = self._get_pool(result)
        if pool is None:
            return []
        try:
            with pool.connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql, params)
                    return cursor.fetchall()
        except Exception as exc:  # noqa: BLE001 - degrade any query failure to a warning
            result.warnings.append(f"{self.name}: Postgres read failed: {exc}")
            return []


class _Warns:
    """Minimal warnings sink so _execute can append to an Aggregates result."""

    def __init__(self, aggregates: Aggregates) -> None:
        self.warnings = aggregates.warnings
