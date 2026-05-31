from __future__ import annotations

from pathlib import Path

from cairn_mcp.backends import JSONLBackend
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import LogQuery, SearchFilters, SortOrder


def _backend(tmp_path: Path) -> JSONLBackend:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-05-30T07:00:00Z","severity":"INFO","msg":"start"}',
                '{"ts":"2026-05-30T07:01:00Z","severity":"ERROR","msg":"boom","req":"req-1"}',
                "not-json",
                '{"ts":"2026-05-30T07:02:00Z","severity":"WARNING","msg":"slow"}',
            ]
        ),
        encoding="utf-8",
    )
    return JSONLBackend(
        "app",
        SourceConfig(
            type="jsonl",
            path=str(path),
            field_map={
                "timestamp": "ts",
                "level": "severity",
                "message": "msg",
                "request_id": "req",
            },
        ),
    )


def test_jsonl_backend_reads_filters_and_derives_ids(tmp_path: Path) -> None:
    result = _backend(tmp_path).fetch(LogQuery(filters=SearchFilters(levels="ERROR")))

    assert [entry.id for entry in result.entries] == ["app:2"]
    assert result.entries[0].request_id == "req-1"
    assert any("invalid JSONL skipped" in warning for warning in result.warnings)


def test_jsonl_backend_orders_and_limits(tmp_path: Path) -> None:
    result = _backend(tmp_path).fetch(
        LogQuery(filters=SearchFilters(), order=SortOrder.DESC, limit=2)
    )

    # newest first, capped at the requested limit
    assert [entry.id for entry in result.entries] == ["app:4", "app:2"]


def test_jsonl_aggregate_surfaces_malformed_line_warnings(tmp_path: Path) -> None:
    result = _backend(tmp_path).aggregate(SearchFilters(), time_bucket="hour")

    assert result.total == 3  # the 3 valid rows
    assert any("invalid JSONL skipped" in warning for warning in result.warnings)


def test_jsonl_backend_get_by_id_uses_source_id_when_present(tmp_path: Path) -> None:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        '{"id":"explicit-1","timestamp":"2026-05-30T07:00:00Z","level":"ERROR","message":"boom"}',
        encoding="utf-8",
    )
    backend = JSONLBackend("app", SourceConfig(type="jsonl", path=str(path)))

    result = backend.get_by_id("explicit-1")

    assert result.entries[0].id == "explicit-1"


def test_jsonl_fetch_returns_bounded_top_n_in_order(tmp_path: Path) -> None:
    path = tmp_path / "logs.jsonl"
    rows = [
        f'{{"ts":"2026-05-30T07:{i:02d}:00Z","severity":"INFO","msg":"m{i}"}}'
        for i in range(40)
    ]
    path.write_text("\n".join(rows), encoding="utf-8")
    backend = JSONLBackend(
        "app",
        SourceConfig(
            type="jsonl",
            path=str(path),
            field_map={"timestamp": "ts", "level": "severity", "message": "msg"},
        ),
    )

    newest = backend.fetch(LogQuery(filters=SearchFilters(), order=SortOrder.DESC, limit=3))
    assert [e.message for e in newest.entries] == ["m39", "m38", "m37"]

    oldest = backend.fetch(LogQuery(filters=SearchFilters(), order=SortOrder.ASC, limit=3))
    assert [e.message for e in oldest.entries] == ["m0", "m1", "m2"]
