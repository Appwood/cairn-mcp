from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cairn_mcp.backends.base import BackendResult, LogBackend
from cairn_mcp.schema import (
    LogEntry,
    LogQuery,
    SearchFilters,
    SortOrder,
    cursor_after,
    entry_matches,
    entry_sort_key,
)


BASE = datetime(2026, 5, 30, 7, 0, tzinfo=UTC)


def _entries(count: int) -> list[LogEntry]:
    return [
        LogEntry(
            id=f"mem:{i}",
            timestamp=BASE + timedelta(seconds=i),
            level="INFO" if i % 2 else "ERROR",
            message=f"row {i}",
            source="mem",
        )
        for i in range(count)
    ]


class MemoryBackend(LogBackend):
    """A faithful pluggable backend that does NOT override aggregate(), so it
    exercises the base default paging path."""

    type_name = "memory_agg_test"

    def __init__(self, entries: list[LogEntry]) -> None:
        self.entries = entries

    def fetch(self, query: LogQuery) -> BackendResult:
        matched = [
            entry
            for entry in self.entries
            if entry_matches(entry, query.filters, include_text=False)
            and (query.cursor is None or cursor_after(entry, query.cursor, query.order))
        ]
        matched.sort(key=entry_sort_key, reverse=query.order == SortOrder.DESC)
        return BackendResult(entries=matched[: query.limit])

    def get_by_id(self, log_id: str) -> BackendResult:
        return BackendResult(entries=[e for e in self.entries if e.id == log_id][:1])


def test_default_scan_does_not_duplicate_or_drop_across_batches() -> None:
    backend = MemoryBackend(_entries(5))

    scanned = list(backend._scan(SearchFilters(), batch=2))

    # exactly the input, in order — no boundary entry repeated or skipped
    assert [e.id for e in scanned] == [e.id for e in backend.entries]


def test_default_aggregate_total_is_exact_across_the_batch_boundary() -> None:
    # 501 entries forces a page boundary at the hard-coded batch size of 500.
    backend = MemoryBackend(_entries(501))

    aggregates = backend.aggregate(SearchFilters(), time_bucket="hour")

    assert aggregates.total == 501
    assert sum(aggregates.by_level.values()) == 501


class _NonAdvancingBackend(LogBackend):
    """A misbehaving backend that ignores the cursor and returns a full batch
    every time — used to prove the base aggregate can't loop forever."""

    type_name = "nonadvancing_test"

    def __init__(self) -> None:
        self.name = "nonadvancing"
        self.calls = 0

    def fetch(self, query: LogQuery) -> BackendResult:
        self.calls += 1
        rows = [
            LogEntry(
                id=f"x{i}",
                timestamp=BASE + timedelta(seconds=i),
                level="INFO",
                message="m",
                source="mem",
            )
            for i in range(query.limit)
        ]
        return BackendResult(entries=rows)

    def get_by_id(self, log_id: str) -> BackendResult:
        return BackendResult()


def test_default_aggregate_terminates_on_non_advancing_backend() -> None:
    backend = _NonAdvancingBackend()

    aggregates = backend.aggregate(SearchFilters(), time_bucket="hour")

    assert backend.calls <= 3   # stopped quickly instead of looping
    assert any("did not advance" in w for w in aggregates.warnings)
