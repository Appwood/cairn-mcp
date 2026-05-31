from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from typing import ClassVar

from cairn_mcp.config import SourceConfig
from cairn_mcp.schema import (
    Cursor,
    LogEntry,
    LogQuery,
    SearchFilters,
    SortOrder,
    bucket_timestamp,
)


@dataclass
class BackendResult:
    entries: list[LogEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Aggregates:
    total: int = 0
    by_level: Counter[str] = field(default_factory=Counter)
    by_service: Counter[str] = field(default_factory=Counter)
    by_logger: Counter[str] = field(default_factory=Counter)
    by_time_bucket: Counter[str] = field(default_factory=Counter)
    warnings: list[str] = field(default_factory=list)

    def merge(self, other: "Aggregates") -> None:
        self.total += other.total
        self.by_level.update(other.by_level)
        self.by_service.update(other.by_service)
        self.by_logger.update(other.by_logger)
        self.by_time_bucket.update(other.by_time_bucket)
        self.warnings.extend(other.warnings)


class LogBackend(ABC):
    #: Source ``type`` this backend handles; used by the registry.
    type_name: ClassVar[str] = ""
    #: Backstop for the default paging aggregate, so a third-party backend over
    #: a huge or misbehaving source can't scan unbounded or loop forever.
    aggregate_scan_limit: ClassVar[int] = 1_000_000

    def __init__(self, name: str, config: SourceConfig) -> None:
        self.name = name
        self.config = config

    @classmethod
    def validate_config(cls, name: str, config: SourceConfig) -> None:
        """Raise ValueError if ``config`` is missing fields this backend needs.

        Called once at startup so misconfiguration fails fast with a clear
        message instead of at first query.
        """

    @abstractmethod
    def fetch(self, query: LogQuery) -> BackendResult:
        """Return up to ``query.limit`` matching entries, ordered and resumable.

        See :class:`~cairn_mcp.schema.LogQuery` for the
        contract. Free-text is intentionally not applied here.
        """
        raise NotImplementedError

    @abstractmethod
    def get_by_id(self, log_id: str) -> BackendResult:
        raise NotImplementedError

    def aggregate(self, filters: SearchFilters, *, time_bucket: str) -> Aggregates:
        """Count matching entries by level, service, logger, and time bucket.

        The default implementation streams the source via :meth:`fetch`. Backends
        that can group natively (SQL ``GROUP BY``) should override this.
        """
        aggregates = Aggregates()
        warnings: list[str] = []
        for entry in self._scan(filters, warnings=warnings):
            aggregates.total += 1
            aggregates.by_level[entry.level] += 1
            aggregates.by_service[entry.service or "unknown"] += 1
            aggregates.by_logger[entry.logger or "unknown"] += 1
            aggregates.by_time_bucket[bucket_timestamp(entry.timestamp, time_bucket)] += 1
        # Paging re-reads can repeat the same parse warning; keep each once.
        seen: set[str] = set()
        for warning in warnings:
            if warning not in seen:
                seen.add(warning)
                aggregates.warnings.append(warning)
        return aggregates

    def _scan(
        self,
        filters: SearchFilters,
        *,
        batch: int = 500,
        warnings: list[str] | None = None,
    ):
        """Yield every matching entry by paging through :meth:`fetch`."""
        cursor: Cursor | None = None
        scanned = 0
        while True:
            result = self.fetch(
                LogQuery(filters=filters, order=SortOrder.ASC, limit=batch, cursor=cursor)
            )
            if warnings is not None:
                warnings.extend(result.warnings)
            if not result.entries:
                break
            yield from result.entries
            scanned += len(result.entries)
            if len(result.entries) < batch:
                break
            if scanned >= self.aggregate_scan_limit:
                if warnings is not None:
                    warnings.append(
                        f"{self.name}: aggregate scan limit "
                        f"({self.aggregate_scan_limit}) reached; counts may be incomplete"
                    )
                break
            last = result.entries[-1]
            # Include source: without it the page-boundary entry sorts after the
            # cursor and is re-yielded next page, double-counting in aggregates.
            next_cursor = Cursor(last.timestamp, last.id, last.source or "")
            if next_cursor == cursor:
                # Backend ignored the cursor (no forward progress) — stop rather
                # than loop forever on the same page.
                if warnings is not None:
                    warnings.append(
                        f"{self.name}: aggregate stopped — backend did not advance the cursor"
                    )
                break
            cursor = next_cursor
