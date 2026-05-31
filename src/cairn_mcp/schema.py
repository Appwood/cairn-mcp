from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


NORMALIZED_FIELDS = {
    "id",
    "timestamp",
    "level",
    "message",
    "logger",
    "service",
    "environment",
    "request_id",
    "trace_id",
    "job_id",
    "user_id",
    "exception_type",
    "traceback",
    "attributes",
}

# Normalized fields that backends can filter on with simple equality. Used by
# push-down backends to translate filters into native predicates.
EQUALITY_FIELDS = (
    "service",
    "logger",
    "request_id",
    "trace_id",
    "job_id",
)


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class LogEntry(BaseModel):
    """Normalized log shape returned by every backend."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    timestamp: datetime
    level: str
    message: str
    logger: str | None = None
    service: str | None = None
    environment: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    job_id: str | None = None
    user_id: str | None = None
    exception_type: str | None = None
    traceback: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    source: str | None = None

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: Any) -> datetime:
        return parse_timestamp(value)

    @field_validator("level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> str:
        if value is None:
            raise ValueError("level is required")
        return str(value).upper()

    @field_validator("message", mode="before")
    @classmethod
    def _normalize_message(cls, value: Any) -> str:
        if value is None:
            raise ValueError("message is required")
        return str(value)

    @field_validator("id", mode="before")
    @classmethod
    def _coerce_id(cls, value: Any) -> str:
        if value is None:
            raise ValueError("id is required")
        return value if isinstance(value, str) else str(value)

    @field_validator(
        "request_id", "trace_id", "job_id", "user_id", mode="before"
    )
    @classmethod
    def _coerce_optional_id(cls, value: Any) -> str | None:
        # Databases store these as uuid/bigint as often as text; accept any
        # scalar and stringify so a uuid id never drops a whole row.
        if value is None:
            return None
        return value if isinstance(value, str) else str(value)


class SearchFilters(BaseModel):
    levels: set[str] | None = None
    text: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    service: str | None = None
    logger: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    job_id: str | None = None

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _parse_optional_timestamp(cls, value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        return parse_timestamp(value)

    @field_validator("levels", mode="before")
    @classmethod
    def _parse_levels(cls, value: Any) -> set[str] | None:
        if value in (None, "", []):
            return None
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
        else:
            parts = [str(part).strip() for part in value]
        return {part.upper() for part in parts if part}


@dataclass(frozen=True)
class Cursor:
    """Keyset pagination position: the (timestamp, id, source) of the last seen
    entry.

    ``source`` is the final tiebreaker so that two entries from different sources
    that happen to share an explicit id and timestamp remain distinguishable —
    without it, one of them is dropped across a page boundary. Encoded as opaque
    URL-safe base64 so clients treat it as a token and never build it by hand.
    """

    timestamp: datetime
    id: str
    source: str = ""

    def encode(self) -> str:
        payload = json.dumps(
            {
                "ts": self.timestamp.astimezone(UTC).isoformat(),
                "id": self.id,
                "src": self.source,
            },
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")

    @classmethod
    def decode(cls, token: str) -> "Cursor":
        try:
            raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
            data = json.loads(raw)
            return cls(parse_timestamp(data["ts"]), str(data["id"]), str(data.get("src", "")))
        except Exception as exc:  # noqa: BLE001 - surface any malformed token uniformly
            raise ValueError(f"invalid cursor token: {exc}") from exc


@dataclass(frozen=True)
class LogQuery:
    """A bounded, ordered, resumable request handed to a backend.

    Backends MUST return at most ``limit`` entries that match ``filters``
    (ignoring free-text, which the adapter applies redaction-aware), sorted by
    (timestamp, id) in ``order``, strictly after ``cursor``. Backends SHOULD
    push filters, ordering, and the limit down to their source.
    """

    filters: SearchFilters
    order: SortOrder = SortOrder.DESC
    limit: int = 100
    cursor: Cursor | None = None


def entry_sort_key(entry: LogEntry) -> tuple[datetime, str, str]:
    # source is the final tiebreaker so the global order is total even when two
    # sources share an explicit id at the same timestamp.
    return (entry.timestamp, entry.id, entry.source or "")


def cursor_to_key(cursor: Cursor) -> tuple[datetime, str, str]:
    return (cursor.timestamp, cursor.id, cursor.source or "")


def cursor_after(entry: LogEntry, cursor: Cursor, order: SortOrder) -> bool:
    """Return True if ``entry`` falls strictly after ``cursor`` in ``order``."""
    key = entry_sort_key(entry)
    pivot = cursor_to_key(cursor)
    if order == SortOrder.DESC:
        return key < pivot
    return key > pivot


def bucket_timestamp(timestamp: datetime, bucket: str) -> str:
    match bucket:
        case "minute":
            value = timestamp.replace(second=0, microsecond=0)
        case "hour":
            value = timestamp.replace(minute=0, second=0, microsecond=0)
        case "day":
            value = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        case _:
            raise ValueError("time_bucket must be minute, hour, or day")
    return value.isoformat()


def parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, int | float):
        parsed = datetime.fromtimestamp(value, tz=UTC)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("timestamp is required")
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"unsupported timestamp value: {value!r}")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def normalize_record(
    raw: dict[str, Any],
    *,
    source_name: str,
    field_map: dict[str, str],
    fallback_id: str,
) -> LogEntry:
    consumed_keys: set[str] = set()
    normalized: dict[str, Any] = {}

    for field in NORMALIZED_FIELDS:
        source_key = field_map.get(field, field)
        if source_key in raw:
            normalized[field] = raw[source_key]
            consumed_keys.add(source_key)

    # A present-but-NULL id column must still fall back, not become None.
    if normalized.get("id") is None:
        normalized["id"] = fallback_id
    normalized["source"] = source_name

    attributes: dict[str, Any] = {}
    mapped_attributes = normalized.pop("attributes", None)
    if isinstance(mapped_attributes, dict):
        attributes.update(mapped_attributes)
    elif isinstance(mapped_attributes, str) and mapped_attributes.strip():
        try:
            decoded = json.loads(mapped_attributes)
        except json.JSONDecodeError:
            attributes["attributes"] = mapped_attributes
        else:
            if isinstance(decoded, dict):
                attributes.update(decoded)
            else:
                attributes["attributes"] = decoded
    elif mapped_attributes is not None:
        attributes["attributes"] = mapped_attributes

    for key, value in raw.items():
        if key not in consumed_keys:
            attributes[key] = value

    normalized["attributes"] = attributes
    return LogEntry(**normalized)


def entry_matches(
    entry: LogEntry,
    filters: SearchFilters,
    *,
    include_text: bool = True,
) -> bool:
    """Structured match check.

    With ``include_text=False`` the free-text term is ignored; backends use this
    so that text matching happens once, redaction-aware, in the adapter. Matching
    raw log text in the backend would let a caller probe for redacted secrets.
    """
    if filters.levels and entry.level.upper() not in filters.levels:
        return False
    if filters.start_time and entry.timestamp < filters.start_time:
        return False
    if filters.end_time and entry.timestamp > filters.end_time:
        return False
    if filters.service and entry.service != filters.service:
        return False
    if filters.logger and entry.logger != filters.logger:
        return False
    if filters.request_id and entry.request_id != filters.request_id:
        return False
    if filters.trace_id and entry.trace_id != filters.trace_id:
        return False
    if filters.job_id and entry.job_id != filters.job_id:
        return False
    if include_text and filters.text:
        haystack = " ".join(
            [
                entry.message,
                entry.traceback or "",
                json.dumps(entry.attributes, default=str, sort_keys=True),
            ]
        ).lower()
        if filters.text.lower() not in haystack:
            return False
    return True
