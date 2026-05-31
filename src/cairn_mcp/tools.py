from __future__ import annotations

import heapq
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cairn_mcp.backends import (
    Aggregates,
    LogBackend,
    get_backend_class,
)
from cairn_mcp.config import (
    HARD_MAX_RESULTS,
    AdapterConfig,
    ConfigError,
    parse_duration,
)
from cairn_mcp.redaction import Redactor
from cairn_mcp.schema import (
    Cursor,
    LogEntry,
    LogQuery,
    SearchFilters,
    SortOrder,
    bucket_timestamp,
    cursor_after,
    entry_sort_key,
    parse_timestamp,
)

# Warnings enter the same model/client context as redacted evidence, so bound
# them: cap the count, cap each length, and dedup repeats.
MAX_WARNINGS = 20
MAX_WARNING_LENGTH = 200


class LogAdapter:
    def __init__(
        self,
        config: AdapterConfig,
        *,
        now_fn: Any | None = None,
    ) -> None:
        self.config = config
        self.now_fn = now_fn or (lambda: datetime.now(UTC))
        self.redactor = Redactor(config.redaction)
        self._default_window: timedelta = parse_duration(config.default_time_window)
        self.backends = self._build_backends(config)

    # ------------------------------------------------------------------ tools

    def search_logs(
        self,
        *,
        level: str | None = None,
        text: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        page_size = self._effective_limit(limit)
        filters = self._filters(
            level=level,
            text=text,
            start_time=start_time,
            end_time=end_time,
            service=service,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
        )
        page, next_cursor, warnings = self._paginate(
            filters, text, SortOrder.DESC, page_size, self._decode_cursor(cursor)
        )
        return {
            "entries": [
                self.redactor.redact_entry(
                    entry, include_traceback=False, include_attributes=False
                )
                for entry in page
            ],
            "count": len(page),
            "next_cursor": next_cursor.encode() if next_cursor else None,
            "has_more": next_cursor is not None,
            "filters": self._filters_for_response(filters),
            "warnings": self._sanitize_warnings(warnings),
        }

    def get_log_detail(self, *, id: str) -> dict[str, Any]:
        warnings: list[str] = []
        for backend in self.backends:
            result = backend.get_by_id(id)
            warnings.extend(result.warnings)
            if result.entries:
                return {
                    "found": True,
                    "entry": self.redactor.redact_entry(
                        result.entries[0],
                        include_traceback=True,
                        include_attributes=True,
                    ),
                    "warnings": self._sanitize_warnings(warnings),
                }
        return {
            "found": False,
            "entry": None,
            "warnings": self._sanitize_warnings(warnings),
        }

    def get_correlation_logs(
        self,
        *,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not any([request_id, trace_id, job_id]):
            raise ValueError("request_id, trace_id, or job_id is required")
        page_size = self._effective_limit(limit)
        filters = self._filters(
            start_time=start_time,
            end_time=end_time,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
        )
        page, next_cursor, warnings = self._paginate(
            filters, None, SortOrder.ASC, page_size, self._decode_cursor(cursor)
        )
        return {
            "entries": [
                self.redactor.redact_entry(
                    entry, include_traceback=False, include_attributes=False
                )
                for entry in page
            ],
            "count": len(page),
            "next_cursor": next_cursor.encode() if next_cursor else None,
            "has_more": next_cursor is not None,
            "filters": self._filters_for_response(filters),
            "warnings": self._sanitize_warnings(warnings),
        }

    def get_log_stats(
        self,
        *,
        level: str | None = None,
        text: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
        time_bucket: str = "hour",
    ) -> dict[str, Any]:
        if time_bucket not in ("minute", "hour", "day"):
            raise ValueError("time_bucket must be minute, hour, or day")
        filters = self._filters(
            level=level,
            text=text,
            start_time=start_time,
            end_time=end_time,
            service=service,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
        )
        if text:
            aggregates, warnings = self._aggregate_with_text(filters, text, time_bucket)
        else:
            aggregates = Aggregates()
            for backend in self.backends:
                aggregates.merge(backend.aggregate(filters, time_bucket=time_bucket))
            warnings = aggregates.warnings
        return {
            "total": aggregates.total,
            "by_level": dict(aggregates.by_level),
            # service/logger labels are log-derived values that bypass entry
            # redaction; redact the keys so stats can't leak what search masks.
            "by_service": self._redact_buckets(aggregates.by_service),
            "by_logger": self._redact_buckets(aggregates.by_logger),
            "by_time_bucket": dict(aggregates.by_time_bucket),
            "time_bucket": time_bucket,
            "filters": self._filters_for_response(filters),
            "warnings": self._sanitize_warnings(warnings),
        }

    # ------------------------------------------------------------- pagination

    def _paginate(
        self,
        filters: SearchFilters,
        text: str | None,
        order: SortOrder,
        page_size: int,
        cursor: Cursor | None,
    ) -> tuple[list[LogEntry], Cursor | None, list[str]]:
        """k-way merge across backends with redaction-aware text filtering.

        Each backend pushes structured filters + order + limit down and returns
        a sorted stream; we merge, then apply the free-text term against the
        *redacted* projection so redacted secrets are never searchable. The scan
        is bounded by ``max_scan`` and resumes via the returned cursor.
        """
        want = page_size + 1
        collected: list[LogEntry] = []
        warnings: list[str] = []
        scanned = 0
        cur = cursor
        guard_tripped = False

        while len(collected) < want:
            batch = want if text is None else max(want, 200)
            streams: list[list[LogEntry]] = []
            exhausted: list[bool] = []
            for backend in self.backends:
                result = backend.fetch(
                    LogQuery(filters=filters, order=order, limit=batch, cursor=cur)
                )
                warnings.extend(result.warnings)
                streams.append(result.entries)
                exhausted.append(len(result.entries) < batch)

            merged = [
                entry
                for entry in heapq.merge(
                    *streams, key=entry_sort_key, reverse=order == SortOrder.DESC
                )
                if cur is None or cursor_after(entry, cur, order)
            ]
            if not merged:
                break

            for entry in merged:
                scanned += 1
                cur = Cursor(entry.timestamp, entry.id, entry.source or "")
                if self._passes_text(entry, text):
                    collected.append(entry)
                    if len(collected) >= want:
                        break
                if scanned >= self.config.max_scan:
                    guard_tripped = True
                    break

            if len(collected) >= want or guard_tripped or all(exhausted):
                break

        if guard_tripped:
            warnings.append(
                "scan limit reached; results may be incomplete — "
                "narrow the filters or continue with next_cursor"
            )

        has_more = len(collected) > page_size
        page = collected[:page_size]
        next_cursor: Cursor | None = None
        if has_more:
            last = page[-1]
            next_cursor = Cursor(last.timestamp, last.id, last.source or "")
        elif guard_tripped and page and cur is not None:
            next_cursor = cur
        return page, next_cursor, warnings

    def _aggregate_with_text(
        self, filters: SearchFilters, text: str, time_bucket: str
    ) -> tuple[Aggregates, list[str]]:
        aggregates = Aggregates()
        warnings: list[str] = []
        scanned = 0
        cur: Cursor | None = None
        guard_tripped = False

        while scanned < self.config.max_scan:
            batch = 500
            streams: list[list[LogEntry]] = []
            exhausted: list[bool] = []
            for backend in self.backends:
                result = backend.fetch(
                    LogQuery(filters=filters, order=SortOrder.ASC, limit=batch, cursor=cur)
                )
                warnings.extend(result.warnings)
                streams.append(result.entries)
                exhausted.append(len(result.entries) < batch)

            merged = [
                entry
                for entry in heapq.merge(*streams, key=entry_sort_key)
                if cur is None or cursor_after(entry, cur, SortOrder.ASC)
            ]
            if not merged:
                break

            for entry in merged:
                scanned += 1
                cur = Cursor(entry.timestamp, entry.id, entry.source or "")
                if self._passes_text(entry, text):
                    aggregates.total += 1
                    aggregates.by_level[entry.level] += 1
                    aggregates.by_service[entry.service or "unknown"] += 1
                    aggregates.by_logger[entry.logger or "unknown"] += 1
                    aggregates.by_time_bucket[
                        bucket_timestamp(entry.timestamp, time_bucket)
                    ] += 1
                if scanned >= self.config.max_scan:
                    guard_tripped = True
                    break

            if guard_tripped or all(exhausted):
                break

        if guard_tripped:
            warnings.append("stats scan limit reached; counts may be incomplete")
        return aggregates, warnings

    def _passes_text(self, entry: LogEntry, text: str | None) -> bool:
        if not text:
            return True
        redacted = self.redactor.redact_entry(
            entry, include_traceback=True, include_attributes=True
        )
        blob = json.dumps(redacted, default=str).lower()
        return text.lower() in blob

    # ----------------------------------------------------------------- helpers

    def _decode_cursor(self, cursor: str | None) -> Cursor | None:
        if not cursor:
            return None
        return Cursor.decode(cursor)

    def _filters(
        self,
        *,
        level: str | None = None,
        text: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        service: str | None = None,
        logger: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        job_id: str | None = None,
    ) -> SearchFilters:
        parsed_start = parse_timestamp(start_time) if start_time else None
        parsed_end = parse_timestamp(end_time) if end_time else None
        if parsed_start is None and parsed_end is None:
            parsed_end = self.now_fn()
            parsed_start = parsed_end - self._default_window
        elif parsed_start is None and parsed_end is not None:
            parsed_start = parsed_end - self._default_window
        elif parsed_end is None:
            parsed_end = self.now_fn()
        if parsed_start is not None and parsed_end is not None and parsed_start > parsed_end:
            raise ValueError("start_time must be before end_time")
        return SearchFilters(
            levels=level,
            text=text,
            start_time=parsed_start,
            end_time=parsed_end,
            service=service,
            logger=logger,
            request_id=request_id,
            trace_id=trace_id,
            job_id=job_id,
        )

    def _effective_limit(self, limit: int | None) -> int:
        requested = self.config.max_results if limit is None else limit
        if requested < 1:
            raise ValueError("limit must be positive")
        return min(requested, self.config.max_results, HARD_MAX_RESULTS)

    def _filters_for_response(self, filters: SearchFilters) -> dict[str, Any]:
        data = filters.model_dump(mode="json", exclude_none=True)
        if "levels" in data:
            data["levels"] = sorted(data["levels"])
        return data

    def _sanitize_warnings(self, warnings: list[str]) -> list[str]:
        """Redact, truncate, dedup, and cap warnings before they reach the
        client — warnings share the agent's context with redacted evidence and
        must not become an unbounded or unredacted side channel."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for warning in warnings:
            text = self.redactor.redact_text(str(warning))
            if len(text) > MAX_WARNING_LENGTH:
                text = text[:MAX_WARNING_LENGTH] + "… [truncated]"
            if text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        if len(cleaned) > MAX_WARNINGS:
            omitted = len(cleaned) - MAX_WARNINGS
            cleaned = cleaned[:MAX_WARNINGS]
            cleaned.append(f"[{omitted} more warnings suppressed]")
        return cleaned

    def _redact_buckets(self, counter: Any) -> dict[str, int]:
        """Redact stats bucket labels, merging counts when redaction collapses
        two labels (e.g. 'token=A' and 'token=B' both mask to the same key)."""
        out: dict[str, int] = {}
        for label, count in counter.items():
            key = self.redactor.redact_text(str(label))
            out[key] = out.get(key, 0) + count
        return out

    def _build_backends(self, config: AdapterConfig) -> list[LogBackend]:
        backends: list[LogBackend] = []
        for name, source in config.sources.items():
            backend_cls = get_backend_class(source.type)
            try:
                backend_cls.validate_config(name, source)
                backends.append(backend_cls(name, source))
            except ValueError as exc:
                raise ConfigError(f"source {name!r}: {exc}") from exc
        return backends
