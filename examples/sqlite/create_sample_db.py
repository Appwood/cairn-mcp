from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).with_name("app_logs.db")


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE logs (
                created_at TEXT NOT NULL,
                severity TEXT NOT NULL,
                body TEXT NOT NULL,
                logger_name TEXT,
                service TEXT,
                environment TEXT,
                request_id TEXT,
                trace_id TEXT,
                job_id TEXT,
                exception_type TEXT,
                traceback TEXT,
                attributes TEXT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO logs (
                created_at, severity, body, logger_name, service, environment,
                request_id, trace_id, job_id, exception_type, traceback, attributes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-05-30T07:20:00Z",
                    "INFO",
                    "job started",
                    "app.jobs",
                    "worker",
                    "dev",
                    None,
                    "trace-job",
                    "job-300",
                    None,
                    None,
                    '{"queue":"default"}',
                ),
                (
                    "2026-05-30T07:20:03Z",
                    "ERROR",
                    "job failed with password=super-secret",
                    "app.jobs",
                    "worker",
                    "dev",
                    None,
                    "trace-job",
                    "job-300",
                    "RuntimeError",
                    "Traceback (most recent call last):\\nRuntimeError: boom",
                    '{"attempt":1,"token":"abc123"}',
                ),
            ],
        )


if __name__ == "__main__":
    main()
