from __future__ import annotations

import uuid

from cairn_mcp.schema import LogEntry, normalize_record


def test_uuid_and_int_ids_are_coerced_not_dropped() -> None:
    uid = uuid.uuid4()
    entry = normalize_record(
        {"id": uid, "timestamp": "2026-05-30T07:00:00Z", "level": "ERROR", "message": "x"},
        source_name="pg",
        field_map={},
        fallback_id="pg:fallback",
    )
    assert entry.id == str(uid)

    int_entry = normalize_record(
        {"id": 4711, "timestamp": "2026-05-30T07:00:00Z", "level": "INFO", "message": "x"},
        source_name="pg",
        field_map={},
        fallback_id="pg:fallback",
    )
    assert int_entry.id == "4711"


def test_uuid_correlation_ids_are_coerced() -> None:
    rid = uuid.uuid4()
    entry = LogEntry(
        id="1",
        timestamp="2026-05-30T07:00:00Z",
        level="ERROR",
        message="x",
        request_id=rid,
        job_id=99,
    )
    assert entry.request_id == str(rid)
    assert entry.job_id == "99"


def test_null_id_column_falls_back() -> None:
    entry = normalize_record(
        {"id": None, "timestamp": "2026-05-30T07:00:00Z", "level": "INFO", "message": "x"},
        source_name="pg",
        field_map={},
        fallback_id="pg:7",
    )
    assert entry.id == "pg:7"
