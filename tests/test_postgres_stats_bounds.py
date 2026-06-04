"""Regression tests for bounded Postgres stats aggregation.

The GROUP BY push-down for the high-cardinality dimensions (service, logger)
had no LIMIT, so a wide time window over a table with many distinct values
streamed one row per value into memory. They are now bounded to the top-N by
count; the bounded-cardinality dimensions (level, time bucket, total) are not.

The cap query fetches N+1 as an overflow sentinel so "exactly N groups"
(complete) is distinguished from "more than N" (truncated) without a false
"truncated" warning at exactly N.
"""

from __future__ import annotations

from cairn_mcp.backends.postgres import (
    MAX_STATS_GROUPS,
    PostgresBackend,
    build_aggregate_sql,
)
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import SearchFilters


def _capped_backend() -> PostgresBackend:
    return PostgresBackend(
        "pg", SourceConfig(type="postgres", dsn="postgresql://x", table="logs")
    )


def _fake_execute(group_rows):
    def fake_execute(sql, params, result):
        if "information_schema" in sql:
            return [
                {"column_name": c}
                for c in ("id", "timestamp", "level", "message", "service", "logger")
            ]
        if "date_trunc" in sql:
            return [{"bucket": "2026-06-02T10:00:00+00:00", "n": 1}]
        if "GROUP BY bucket" in sql and "upper(" in sql:  # by_level (uncapped)
            return [{"bucket": "INFO", "n": 1}]
        if "GROUP BY bucket" in sql:  # by_service / by_logger (capped)
            return list(group_rows)
        return [{"n": 1}]  # total

    return fake_execute


def test_high_cardinality_dimensions_are_capped() -> None:
    queries = build_aggregate_sql(
        "log_entries", {}, SearchFilters(), time_bucket="hour"
    )

    for dimension in ("by_service", "by_logger"):
        sql, params = queries[dimension]
        assert "ORDER BY n DESC LIMIT %s" in sql, dimension
        # cap + 1: one extra row as the overflow sentinel
        assert params[-1] == MAX_STATS_GROUPS + 1


def test_bounded_cardinality_dimensions_are_not_capped() -> None:
    queries = build_aggregate_sql(
        "log_entries", {}, SearchFilters(), time_bucket="hour"
    )

    assert "LIMIT" not in queries["by_level"][0]
    assert "LIMIT" not in queries["total"][0]
    assert "LIMIT" not in queries["by_time_bucket"][0]


def test_exactly_cap_groups_is_complete_and_does_not_warn(monkeypatch) -> None:
    backend = _capped_backend()
    rows = [{"bucket": f"svc-{i}", "n": 1} for i in range(MAX_STATS_GROUPS)]
    monkeypatch.setattr(backend, "_execute", _fake_execute(rows))

    agg = backend.aggregate(SearchFilters(), time_bucket="hour")

    assert not any("truncated" in warning for warning in agg.warnings)
    assert len(agg.by_service) == MAX_STATS_GROUPS


def test_more_than_cap_groups_warns_and_drops_sentinel(monkeypatch) -> None:
    backend = _capped_backend()
    # the real query fetches cap+1; simulate that overflow sentinel being present
    rows = [{"bucket": f"svc-{i}", "n": 1} for i in range(MAX_STATS_GROUPS + 1)]
    monkeypatch.setattr(backend, "_execute", _fake_execute(rows))

    agg = backend.aggregate(SearchFilters(), time_bucket="hour")

    assert any("truncated" in warning for warning in agg.warnings)
    assert len(agg.by_service) == MAX_STATS_GROUPS  # sentinel row dropped
