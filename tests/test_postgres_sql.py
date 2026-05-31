from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cairn_mcp.backends.postgres import (
    PostgresBackend,
    build_aggregate_sql,
    build_fetch_sql,
)
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import Cursor, LogQuery, SearchFilters, SortOrder


def test_fetch_sql_pushes_filters_order_cursor_and_limit() -> None:
    cursor = Cursor(datetime(2026, 5, 30, 7, 0, tzinfo=UTC), "id-9", "pg")
    query = LogQuery(
        filters=SearchFilters(
            levels="ERROR",
            start_time="2026-05-30T00:00:00Z",
            end_time="2026-05-30T08:00:00Z",
            request_id="req-1",
        ),
        order=SortOrder.DESC,
        limit=50,
        cursor=cursor,
    )

    sql, params = build_fetch_sql("log_entries", {}, query, source_name="pg")

    assert "upper(\"level\"::text) = ANY(%s)" in sql
    assert '"timestamp" >= %s' in sql and '"timestamp" <= %s' in sql
    # equality filters cast to text so a uuid column never hits
    # "operator does not exist: uuid = text" under psycopg server-side binding
    assert '"request_id"::text = %s' in sql
    assert '"request_id" = %s' not in sql
    # keyset pagination over (timestamp, id, source) with byte-stable collation
    assert (
        '("timestamp", "id"::text COLLATE "C", %s::text COLLATE "C") < (%s, %s, %s)'
        in sql
    )
    assert 'ORDER BY "timestamp" DESC, "id"::text COLLATE "C" DESC' in sql
    assert sql.strip().endswith("LIMIT %s")
    assert params[0] == ["ERROR"]
    # ... source_name (lhs constant), then cursor ts/id/source, then limit
    assert params[-5:] == ["pg", cursor.timestamp, "id-9", "pg", 50]


def test_equality_filters_are_text_cast_for_every_field() -> None:
    # Guards against re-introducing "operator does not exist: uuid = text" for
    # any equality-filtered column (request_id is uuid in the documented schema).
    query = LogQuery(
        filters=SearchFilters(
            service="checkout",
            logger="app.http",
            request_id="r-1",
            trace_id="t-1",
            job_id="j-1",
        ),
        limit=10,
    )

    sql, _ = build_fetch_sql("log_entries", {}, query)

    for column in ("service", "logger", "request_id", "trace_id", "job_id"):
        assert f'"{column}"::text = %s' in sql
        assert f'"{column}" = %s' not in sql


def test_fetch_sql_ascending_uses_greater_than_keyset() -> None:
    cursor = Cursor(datetime(2026, 5, 30, 7, 0, tzinfo=UTC), "id-1", "pg")
    query = LogQuery(filters=SearchFilters(), order=SortOrder.ASC, limit=10, cursor=cursor)

    sql, _ = build_fetch_sql("log_entries", {}, query, source_name="pg")

    assert (
        '("timestamp", "id"::text COLLATE "C", %s::text COLLATE "C") > (%s, %s, %s)'
        in sql
    )
    assert 'ORDER BY "timestamp" ASC, "id"::text COLLATE "C" ASC' in sql


def test_fetch_sql_honours_field_map_and_schema() -> None:
    query = LogQuery(filters=SearchFilters(job_id="job-1"), limit=5)
    sql, params = build_fetch_sql(
        "logs",
        {"timestamp": "created_at", "id": "uuid", "job_id": "job"},
        query,
        schema="audit",
    )

    assert 'FROM "audit"."logs"' in sql
    assert '"job"::text = %s' in sql
    assert 'ORDER BY "created_at" DESC, "uuid"::text COLLATE "C" DESC' in sql
    assert params == ["job-1", 5]


def test_fetch_sql_rejects_unsafe_identifiers() -> None:
    query = LogQuery(filters=SearchFilters(), limit=5)
    with pytest.raises(ValueError):
        build_fetch_sql("logs; DROP TABLE logs", {}, query)


def test_aggregate_sql_groups_each_dimension() -> None:
    queries = build_aggregate_sql(
        "log_entries", {}, SearchFilters(levels="ERROR"), time_bucket="hour"
    )

    assert set(queries) == {"total", "by_level", "by_service", "by_logger", "by_time_bucket"}
    assert "count(*)" in queries["total"][0]
    assert "GROUP BY bucket" in queries["by_level"][0]
    bucket_sql = queries["by_time_bucket"][0]
    assert "date_trunc(%s" in bucket_sql
    # bucket keys must match datetime.isoformat(): UTC truncation + "+00:00"
    assert "AT TIME ZONE 'UTC'" in bucket_sql
    assert "|| '+00:00'" in bucket_sql
    # time_bucket is the first param of the bucketed query, then the level filter
    assert queries["by_time_bucket"][1][0] == "hour"


def test_aggregate_sql_rejects_bad_time_bucket() -> None:
    with pytest.raises(ValueError):
        build_aggregate_sql("logs", {}, SearchFilters(), time_bucket="fortnight")


def test_aggregate_sql_skips_optional_columns_a_minimal_table_lacks() -> None:
    # the documented Postgres example maps logger -> logger_name and omits service
    field_map = {"logger": "logger_name"}
    available = {"id", "timestamp", "level", "message", "logger_name"}

    queries = build_aggregate_sql(
        "log_entries",
        field_map,
        SearchFilters(),
        time_bucket="hour",
        available_columns=available,
    )

    assert "by_service" not in queries  # no 'service' column -> no broken query
    assert "by_logger" in queries  # logger_name exists -> still aggregated
    assert {"total", "by_level", "by_time_bucket"} <= set(queries)


def test_aggregate_sql_includes_all_dimensions_when_columns_unknown() -> None:
    queries = build_aggregate_sql(
        "logs", {}, SearchFilters(), time_bucket="hour", available_columns=None
    )
    assert "by_service" in queries and "by_logger" in queries


def test_aggregate_reports_absent_dimension_as_unknown(monkeypatch) -> None:
    backend = PostgresBackend(
        "pg",
        SourceConfig(
            type="postgres",
            dsn="postgresql://x",
            table="log_entries",
            field_map={"logger": "logger_name"},
        ),
    )

    def fake_execute(sql, params, result):
        if "information_schema.columns" in sql:
            # the table has logger_name but NO service column
            return [{"column_name": c} for c in ("id", "timestamp", "level", "logger_name")]
        if "date_trunc" in sql:
            return [{"bucket": "2026-05-30T10:00:00+00:00", "n": 5}]
        if "GROUP BY bucket" in sql and "upper(" in sql:
            return [{"bucket": "ERROR", "n": 3}, {"bucket": "INFO", "n": 2}]
        if "GROUP BY bucket" in sql:  # by_logger
            return [{"bucket": "app.jobs", "n": 5}]
        return [{"n": 5}]  # total

    monkeypatch.setattr(backend, "_execute", fake_execute)

    agg = backend.aggregate(SearchFilters(), time_bucket="hour")

    assert agg.total == 5
    assert dict(agg.by_level) == {"ERROR": 3, "INFO": 2}
    assert dict(agg.by_logger) == {"app.jobs": 5}
    assert dict(agg.by_time_bucket) == {"2026-05-30T10:00:00+00:00": 5}
    # service column is absent -> every row counts as "unknown", not empty
    assert dict(agg.by_service) == {"unknown": 5}


# --- connection pooling / _execute lifecycle (no live DB; fake pool seam) ---


class _FakeCursor:
    def __init__(self, rows, raise_exc=None):
        self._rows, self._raise = rows, raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if self._raise:
            raise self._raise

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


class _FakePool:
    def __init__(self, rows=None, raise_exc=None):
        self.rows, self.raise_exc = rows or [], raise_exc
        self.borrows, self.closed = 0, False

    def connection(self):
        self.borrows += 1
        return _FakeConn(_FakeCursor(self.rows, self.raise_exc))

    def close(self):
        self.closed = True


class _PooledBackend(PostgresBackend):
    def __init__(self, *args, fake=None, fail=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.create_calls = 0
        self._fake, self._fail = fake, fail

    def _create_pool(self):
        self.create_calls += 1
        if self._fail:
            raise self._fail
        return self._fake


class _Sink:
    def __init__(self):
        self.warnings = []


def _pg_cfg():
    return SourceConfig(type="postgres", dsn="postgresql://x", table="logs")


def test_execute_reuses_a_single_pool_across_queries() -> None:
    fake = _FakePool(rows=[{"n": 1}])
    backend = _PooledBackend("pg", _pg_cfg(), fake=fake)
    sink = _Sink()

    results = [backend._execute("SELECT 1", [], sink) for _ in range(3)]

    assert all(r == [{"n": 1}] for r in results)
    assert backend.create_calls == 1   # pool built once...
    assert fake.borrows == 3            # ...and reused for every query
    assert sink.warnings == []


def test_execute_degrades_and_caches_failure_when_driver_missing() -> None:
    backend = _PooledBackend("pg", _pg_cfg(), fail=ImportError("no psycopg"))
    sink = _Sink()

    assert backend._execute("SELECT 1", [], sink) == []
    assert any("psycopg" in w for w in sink.warnings)

    backend._execute("SELECT 1", [], sink)
    assert backend.create_calls == 1   # failure cached: no retry storm


def test_execute_degrades_on_query_error() -> None:
    backend = _PooledBackend("pg", _pg_cfg(), fake=_FakePool(raise_exc=RuntimeError("boom")))
    sink = _Sink()

    assert backend._execute("SELECT 1", [], sink) == []
    assert any("Postgres read failed" in w for w in sink.warnings)


def test_postgres_validate_config_requires_dsn_and_table() -> None:
    with pytest.raises(ValueError):
        PostgresBackend.validate_config(
            "pg", SourceConfig(type="postgres", table="logs")
        )
    with pytest.raises(ValueError):
        PostgresBackend.validate_config(
            "pg", SourceConfig(type="postgres", dsn="postgresql://x")
        )
    # valid: both present
    PostgresBackend.validate_config(
        "pg", SourceConfig(type="postgres", dsn="postgresql://x", table="logs")
    )
