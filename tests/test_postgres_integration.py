from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from cairn_mcp.config import AdapterConfig, SourceConfig
from cairn_mcp.tools import LogAdapter


POSTGRES_TEST_DSN = "CAIRN_POSTGRES_TEST_DSN"


pytestmark = pytest.mark.integration


@pytest.fixture()
def postgres_logs_table():
    dsn = os.environ.get(POSTGRES_TEST_DSN)
    if not dsn:
        pytest.skip(f"set {POSTGRES_TEST_DSN} to run Postgres integration tests")
    try:
        import psycopg
        from psycopg import sql
    except ImportError:
        pytest.skip("install the postgres extra to run Postgres integration tests")

    schema = f"sla_mcp_test_{uuid.uuid4().hex}"
    table = "logs"

    # Create the schema BEFORE the try: if this fails nothing was created, so
    # there is nothing to drop and we don't mask the real error in teardown.
    with psycopg.connect(dsn, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    # Everything after the schema exists is wrapped so a failure during table
    # creation or insert still drops the schema instead of leaking it.
    try:
        request_id = uuid.uuid4()
        rows = [
            (
                uuid.uuid4(),
                datetime(2026, 5, 30, 7, 0, tzinfo=UTC),
                "INFO",
                "request started",
                "app.http",
                "checkout",
                request_id,
                "trace-pg",
                None,
                None,
                '{"path": "/checkout"}',
            ),
            (
                uuid.uuid4(),
                datetime(2026, 5, 30, 7, 1, tzinfo=UTC),
                "ERROR",
                "payment failed token=pg-secret",
                "app.payments",
                "checkout",
                request_id,
                "trace-pg",
                None,
                "Traceback: bearer abc.def",
                '{"attempt": 1, "api_key": "do-not-return"}',
            ),
            (
                uuid.uuid4(),
                datetime(2026, 5, 30, 7, 2, tzinfo=UTC),
                "WARNING",
                "retry scheduled",
                "app.payments",
                "checkout",
                request_id,
                "trace-pg",
                None,
                None,
                '{"attempt": 2}',
            ),
        ]
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        """
                        CREATE TABLE {}.{} (
                            id uuid PRIMARY KEY,
                            timestamp timestamptz NOT NULL,
                            level text NOT NULL,
                            message text NOT NULL,
                            logger_name text,
                            service text,
                            request_id uuid,
                            trace_id text,
                            job_id text,
                            traceback text,
                            extra jsonb
                        )
                        """
                    ).format(sql.Identifier(schema), sql.Identifier(table))
                )
                cursor.executemany(
                    sql.SQL(
                        """
                        INSERT INTO {}.{} (
                            id, timestamp, level, message, logger_name, service,
                            request_id, trace_id, job_id, traceback, extra
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """
                    ).format(sql.Identifier(schema), sql.Identifier(table)),
                    rows,
                )

        yield {
            "dsn": dsn,
            "schema": schema,
            "table": table,
            "request_id": str(request_id),
            "error_id": str(rows[1][0]),
        }
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(schema)
                    )
                )


def test_postgres_backend_smoke(postgres_logs_table) -> None:
    adapter = LogAdapter(
        AdapterConfig(
            sources={
                "pg": SourceConfig(
                    type="postgres",
                    dsn=postgres_logs_table["dsn"],
                    table=postgres_logs_table["table"],
                    options={"schema": postgres_logs_table["schema"]},
                    field_map={
                        "id": "id",
                        "timestamp": "timestamp",
                        "level": "level",
                        "message": "message",
                        "logger": "logger_name",
                        "request_id": "request_id",
                        "trace_id": "trace_id",
                        "job_id": "job_id",
                        "traceback": "traceback",
                        "attributes": "extra",
                    },
                )
            },
            max_results=1,
        )
    )

    first = adapter.search_logs(
        level="ERROR,WARNING",
        start_time="2026-05-30T07:00:00Z",
        end_time="2026-05-30T08:00:00Z",
    )
    # No warnings: a warning here means a query silently failed (e.g. a bad cast).
    assert first["warnings"] == []
    assert first["count"] == 1
    assert first["has_more"] is True
    assert first["entries"][0]["level"] == "WARNING"

    second = adapter.search_logs(
        level="ERROR,WARNING",
        start_time="2026-05-30T07:00:00Z",
        end_time="2026-05-30T08:00:00Z",
        cursor=first["next_cursor"],
    )
    assert second["warnings"] == []
    assert second["count"] == 1
    assert second["entries"][0]["level"] == "ERROR"
    assert "pg-secret" not in second["entries"][0]["message"]

    detail = adapter.get_log_detail(id=postgres_logs_table["error_id"])
    assert detail["warnings"] == []
    assert detail["found"] is True
    assert detail["entry"]["id"] == postgres_logs_table["error_id"]
    assert "abc.def" not in detail["entry"]["traceback"]
    assert detail["entry"]["attributes"]["api_key"] == "[REDACTED]"

    # Correlation filters on request_id, which is a uuid column: this is exactly
    # the path that fails without the ::text cast in the equality filter.
    correlated = adapter.get_correlation_logs(
        request_id=postgres_logs_table["request_id"],
        start_time="2026-05-30T07:00:00Z",
        end_time="2026-05-30T08:00:00Z",
        limit=10,
    )
    assert correlated["warnings"] == []
    assert [entry["level"] for entry in correlated["entries"]] == [
        "INFO",
        "ERROR",
        "WARNING",
    ]

    stats = adapter.get_log_stats(
        start_time="2026-05-30T07:00:00Z",
        end_time="2026-05-30T08:00:00Z",
        time_bucket="hour",
    )
    assert stats["warnings"] == []
    assert stats["total"] == 3
    assert stats["by_level"] == {"ERROR": 1, "INFO": 1, "WARNING": 1}
    assert stats["by_service"] == {"checkout": 3}
    assert stats["by_time_bucket"] == {"2026-05-30T07:00:00+00:00": 3}

    # Connections are pooled: the many queries above ran over a handful of
    # reused connections, not one TCP/auth handshake per query.
    pool = adapter.backends[0]._pool
    if pool is not None and hasattr(pool, "get_stats"):
        pstats = pool.get_stats()
        if "requests_num" in pstats and "connections_num" in pstats:
            assert pstats["requests_num"] >= 5          # many borrows
            assert pstats["connections_num"] <= 4       # few real connections
