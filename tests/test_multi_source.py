from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from cairn_mcp.config import AdapterConfig, SourceConfig
from cairn_mcp.tools import LogAdapter


NOW = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)


def _adapter(tmp_path: Path, *, max_results: int = 100) -> LogAdapter:
    jsonl_path = tmp_path / "logs.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [
                '{"ts":"2026-05-30T10:00:00Z","severity":"INFO","msg":"j-a"}',
                '{"ts":"2026-05-30T10:02:00Z","severity":"INFO","msg":"j-b"}',
                '{"ts":"2026-05-30T10:04:00Z","severity":"INFO","msg":"j-c"}',
            ]
        ),
        encoding="utf-8",
    )
    db_path = tmp_path / "logs.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE logs (created_at TEXT, severity TEXT, body TEXT)"
        )
        connection.executemany(
            "INSERT INTO logs VALUES (?, ?, ?)",
            [
                ("2026-05-30T10:01:00Z", "INFO", "s-a"),
                ("2026-05-30T10:03:00Z", "INFO", "s-b"),
                ("2026-05-30T10:05:00Z", "INFO", "s-c"),
            ],
        )
    config = AdapterConfig(
        max_results=max_results,
        default_time_window="24h",
        sources={
            "j": SourceConfig(
                type="jsonl",
                path=str(jsonl_path),
                field_map={"timestamp": "ts", "level": "severity", "message": "msg"},
            ),
            "s": SourceConfig(
                type="sqlite",
                path=str(db_path),
                table="logs",
                field_map={
                    "timestamp": "created_at",
                    "level": "severity",
                    "message": "body",
                },
            ),
        },
    )
    return LogAdapter(config, now_fn=lambda: NOW)


def test_search_merges_sources_in_global_timestamp_order(tmp_path: Path) -> None:
    result = _adapter(tmp_path).search_logs()

    messages = [entry["message"] for entry in result["entries"]]
    # interleaved newest-first across both backends
    assert messages == ["s-c", "j-c", "s-b", "j-b", "s-a", "j-a"]


def test_cursor_pagination_across_sources_is_complete_and_ordered(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, max_results=2)

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = adapter.search_logs(limit=2, cursor=cursor)
        seen.extend(entry["message"] for entry in page["entries"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert seen == ["s-c", "j-c", "s-b", "j-b", "s-a", "j-a"]
    assert len(seen) == len(set(seen))  # no duplicates across page boundaries


def test_stats_aggregate_across_sources(tmp_path: Path) -> None:
    result = _adapter(tmp_path).get_log_stats(time_bucket="hour")

    assert result["total"] == 6
    assert result["by_level"] == {"INFO": 6}


def test_colliding_explicit_ids_across_sources_are_not_dropped(tmp_path: Path) -> None:
    # Two sources each carry an explicit id "X" at the SAME timestamp. The source
    # tiebreaker must keep both reachable across a page boundary.
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    fm = {"timestamp": "ts", "level": "severity", "message": "msg"}
    a.write_text(
        '{"id":"X","ts":"2026-05-30T10:00:00Z","severity":"INFO","msg":"from-A"}\n',
        encoding="utf-8",
    )
    b.write_text(
        '{"id":"X","ts":"2026-05-30T10:00:00Z","severity":"INFO","msg":"from-B"}\n',
        encoding="utf-8",
    )
    config = AdapterConfig(
        max_results=1,
        sources={
            "a": SourceConfig(type="jsonl", path=str(a), field_map=fm),
            "b": SourceConfig(type="jsonl", path=str(b), field_map=fm),
        },
    )
    adapter = LogAdapter(config, now_fn=lambda: NOW)

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        page = adapter.search_logs(limit=1, cursor=cursor)
        seen.extend(entry["message"] for entry in page["entries"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert sorted(seen) == ["from-A", "from-B"]
