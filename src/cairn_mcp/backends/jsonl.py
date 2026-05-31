from __future__ import annotations

import heapq
import json
from collections.abc import Iterator
from pathlib import Path

from cairn_mcp.backends.base import BackendResult, LogBackend
from cairn_mcp.backends.registry import register_backend
from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import (
    LogEntry,
    LogQuery,
    SearchFilters,
    SortOrder,
    cursor_after,
    entry_matches,
    entry_sort_key,
    normalize_record,
)


@register_backend
class JSONLBackend(LogBackend):
    type_name = "jsonl"

    @classmethod
    def validate_config(cls, name: str, config: SourceConfig) -> None:
        if not config.path:
            raise ValueError(f"jsonl source {name!r} requires a path")

    def fetch(self, query: LogQuery) -> BackendResult:
        result = BackendResult()
        candidates = (
            entry
            for entry in self._entries(result)
            if entry_matches(entry, query.filters, include_text=False)
            and (query.cursor is None or cursor_after(entry, query.cursor, query.order))
        )
        # Bounded top-N: keep only `limit` entries (O(limit) memory) and skip the
        # full-list sort. The file is still read once end to end — JSONL is
        # unindexed, so finding the newest/oldest N means examining every row;
        # use the SQLite or Postgres backend for scale.
        if query.order == SortOrder.DESC:
            result.entries = heapq.nlargest(query.limit, candidates, key=entry_sort_key)
        else:
            result.entries = heapq.nsmallest(query.limit, candidates, key=entry_sort_key)
        return result

    def get_by_id(self, log_id: str) -> BackendResult:
        result = BackendResult()
        for entry in self._entries(result):
            if entry.id == log_id:
                result.entries.append(entry)
                break
        return result

    def aggregate(self, filters: SearchFilters, *, time_bucket: str):
        # One streaming pass (vs. the paging base default) so malformed-line
        # warnings are surfaced once and the file is read only once.
        from cairn_mcp.backends.base import Aggregates
        from cairn_mcp.schema import bucket_timestamp

        result = BackendResult()
        aggregates = Aggregates()
        for entry in self._entries(result):
            if not entry_matches(entry, filters, include_text=False):
                continue
            aggregates.total += 1
            aggregates.by_level[entry.level] += 1
            aggregates.by_service[entry.service or "unknown"] += 1
            aggregates.by_logger[entry.logger or "unknown"] += 1
            aggregates.by_time_bucket[bucket_timestamp(entry.timestamp, time_bucket)] += 1
        aggregates.warnings.extend(result.warnings)
        return aggregates

    def _entries(self, result: BackendResult) -> Iterator[LogEntry]:
        for line_number, raw in self._records(result):
            try:
                yield normalize_record(
                    raw,
                    source_name=self.name,
                    field_map=self.config.field_map,
                    fallback_id=f"{self.name}:{line_number}",
                )
            except ValueError as exc:
                # value-free: exc text can echo the raw (possibly secret) field value
                result.warnings.append(
                    f"{self.name}:{line_number}: skipped malformed record ({type(exc).__name__})"
                )

    def _records(self, result: BackendResult) -> Iterator[tuple[int, dict]]:
        path = Path(self.config.path or "")
        try:
            handle = path.open("r", encoding="utf-8")
        except OSError as exc:
            result.warnings.append(f"{self.name}: cannot open {path}: {exc}")
            return

        with handle:
            for line_number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    result.warnings.append(
                        f"{self.name}:{line_number}: invalid JSONL skipped: {exc.msg}"
                    )
                    continue
                if not isinstance(decoded, dict):
                    result.warnings.append(
                        f"{self.name}:{line_number}: JSONL row is not an object"
                    )
                    continue
                yield line_number, decoded
