from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cairn_mcp.config import AdapterConfig, SourceConfig
from cairn_mcp.tools import MAX_WARNINGS, LogAdapter


NOW = datetime(2026, 5, 30, 8, 0, tzinfo=UTC)


def _adapter(tmp_path: Path, *, max_results: int = 2) -> LogAdapter:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-05-30T07:00:00Z","severity":"INFO","msg":"start","req":"req-1","trace":"trace-1"}',
                '{"ts":"2026-05-30T07:01:00Z","severity":"WARNING","msg":"slow","req":"req-1","trace":"trace-1"}',
                '{"ts":"2026-05-30T07:02:00Z","severity":"ERROR","msg":"boom token=abc123","req":"req-1","trace":"trace-1","traceback":"Traceback bearer abc.def","secret":"hunter2"}',
                '{"ts":"2026-05-29T06:00:00Z","severity":"ERROR","msg":"old boom"}',
            ]
        ),
        encoding="utf-8",
    )
    config = AdapterConfig(
        max_results=max_results,
        default_time_window="24h",
        sources={
            "app": SourceConfig(
                type="jsonl",
                path=str(path),
                field_map={
                    "timestamp": "ts",
                    "level": "severity",
                    "message": "msg",
                    "request_id": "req",
                    "trace_id": "trace",
                },
            )
        },
    )
    return LogAdapter(config, now_fn=lambda: NOW)


def test_search_logs_enforces_default_window_limit_and_compact_shape(tmp_path: Path) -> None:
    result = _adapter(tmp_path, max_results=1).search_logs(level="ERROR,WARNING", limit=5)

    assert result["count"] == 1
    assert result["has_more"] is True
    assert result["next_cursor"] is not None
    assert result["entries"][0]["level"] == "ERROR"
    assert "abc123" not in result["entries"][0]["message"]
    assert "traceback" not in result["entries"][0]
    assert "attributes" not in result["entries"][0]


def test_search_logs_pages_through_cursor_without_overlap(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, max_results=1)

    first = adapter.search_logs(level="ERROR,WARNING")
    assert [entry["id"] for entry in first["entries"]] == ["app:3"]

    second = adapter.search_logs(level="ERROR,WARNING", cursor=first["next_cursor"])
    assert [entry["id"] for entry in second["entries"]] == ["app:2"]
    assert second["has_more"] is False
    assert second["next_cursor"] is None


def test_text_search_cannot_probe_redacted_secrets(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, max_results=10)

    # the literal token value is redacted, so it must not be searchable (no oracle)
    leaked = adapter.search_logs(text="abc123")
    assert leaked["count"] == 0

    secret_attr = adapter.search_logs(text="hunter2")
    assert secret_attr["count"] == 0

    # non-secret text still matches normally
    visible = adapter.search_logs(text="boom")
    assert [entry["id"] for entry in visible["entries"]] == ["app:3"]


def test_get_log_detail_returns_redacted_traceback_and_attributes(tmp_path: Path) -> None:
    result = _adapter(tmp_path).get_log_detail(id="app:3")

    assert result["found"] is True
    assert "abc.def" not in result["entry"]["traceback"]
    assert result["entry"]["attributes"]["secret"] == "[REDACTED]"


def test_correlation_logs_are_ordered_by_timestamp(tmp_path: Path) -> None:
    result = _adapter(tmp_path).get_correlation_logs(request_id="req-1")

    assert [entry["id"] for entry in result["entries"]] == ["app:1", "app:2"]
    assert result["has_more"] is True
    assert result["next_cursor"] is not None


def test_stats_counts_matching_entries_by_dimensions(tmp_path: Path) -> None:
    result = _adapter(tmp_path, max_results=10).get_log_stats(level="ERROR,WARNING")

    assert result["total"] == 2
    assert result["by_level"] == {"WARNING": 1, "ERROR": 1}
    assert result["by_time_bucket"] == {"2026-05-30T07:00:00+00:00": 2}


def test_stats_text_filter_is_redaction_aware(tmp_path: Path) -> None:
    result = _adapter(tmp_path, max_results=10).get_log_stats(text="abc123")
    assert result["total"] == 0


def test_inverted_time_window_is_rejected(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        _adapter(tmp_path).search_logs(
            start_time="2026-05-30T08:00:00Z", end_time="2026-05-30T07:00:00Z"
        )


def test_get_log_stats_surfaces_backend_warnings(tmp_path: Path) -> None:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"ts":"2026-05-30T07:00:00Z","severity":"INFO","msg":"ok"}',
                "NOT-JSON",
                '{"ts":"2026-05-30T07:01:00Z","severity":"ERROR","msg":"boom"}',
            ]
        ),
        encoding="utf-8",
    )
    config = AdapterConfig(
        sources={
            "app": SourceConfig(
                type="jsonl",
                path=str(path),
                field_map={"timestamp": "ts", "level": "severity", "message": "msg"},
            )
        }
    )
    adapter = LogAdapter(config, now_fn=lambda: NOW)

    stats = adapter.get_log_stats()

    assert stats["total"] == 2
    assert any("invalid JSONL skipped" in warning for warning in stats["warnings"])


def test_stats_redacts_service_and_logger_bucket_labels(tmp_path: Path) -> None:
    path = tmp_path / "logs.jsonl"
    path.write_text(
        '{"ts":"2026-05-30T07:30:00Z","severity":"INFO","msg":"x",'
        '"svc":"api_key=SECRET123","lg":"token=abc"}\n',
        encoding="utf-8",
    )
    config = AdapterConfig(
        sources={
            "app": SourceConfig(
                type="jsonl",
                path=str(path),
                field_map={
                    "timestamp": "ts",
                    "level": "severity",
                    "message": "msg",
                    "service": "svc",
                    "logger": "lg",
                },
            )
        }
    )
    stats = LogAdapter(config, now_fn=lambda: NOW).get_log_stats()

    assert "api_key=[REDACTED]" in stats["by_service"]
    assert all("SECRET123" not in key for key in stats["by_service"])
    assert all("abc" not in key for key in stats["by_logger"])


def test_warnings_are_value_free_capped_and_deduped(tmp_path: Path) -> None:
    # 40 malformed rows (distinct line numbers) carrying a secret-like bad value,
    # plus one valid row so there is a result.
    lines = [
        f'{{"ts":"bad-api_key=SECRET{i}","severity":"INFO","msg":"x"}}'
        for i in range(40)
    ]
    lines.append('{"ts":"2026-05-30T07:30:00Z","severity":"INFO","msg":"ok"}')
    path = tmp_path / "logs.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")
    config = AdapterConfig(
        sources={
            "app": SourceConfig(
                type="jsonl",
                path=str(path),
                field_map={"timestamp": "ts", "level": "severity", "message": "msg"},
            )
        }
    )

    result = LogAdapter(config, now_fn=lambda: NOW).search_logs()

    warnings = result["warnings"]
    # value-free: the raw bad field value never appears in a warning
    assert all("SECRET" not in w for w in warnings)
    # bounded: capped to MAX_WARNINGS plus one suppressed-summary line
    assert len(warnings) == MAX_WARNINGS + 1
    assert "suppressed" in warnings[-1]


def test_sanitize_warnings_redacts_and_dedups(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path)
    out = adapter._sanitize_warnings(
        ["leaked token=SECRET", "leaked token=SECRET"] + [f"w{i}" for i in range(30)]
    )

    assert "SECRET" not in " ".join(out)
    assert out[0] == "leaked token=[REDACTED]"   # redacted + deduped to one copy
    assert len(out) == MAX_WARNINGS + 1
    assert "suppressed" in out[-1]
