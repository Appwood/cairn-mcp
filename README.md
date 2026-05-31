<p align="center">
  <img src="https://raw.githubusercontent.com/Appwood/cairn-mcp/main/branding/cairn-banner.png" alt="Cairn — evidence, not instructions" width="820">
</p>

<p align="center">
  <em>Give coding agents safe, read-only access to the structured logs you already have.</em>
</p>

# Cairn — Structured Log Adapter MCP

`cairn-mcp` is a tiny Python MCP server for querying existing
structured application logs from AI coding tools. It is an adapter, not a log
platform: no ingestion pipeline, no observability UI, no raw SQL tool, and no
write operations.

## Why

Agents can read source code, but debugging runtime behavior still often depends
on pasted log snippets. This adapter lets agents query bounded, redacted log
evidence directly, so they can investigate with request IDs, tracebacks, and
timestamps instead of guessing.

## Features

- Local stdio MCP server using the official Python MCP SDK.
- JSONL, SQLite, and Postgres backends — pluggable via entry points.
- Push-down query contract: backends translate filters, ordering, and limits
  into native queries; the adapter does a k-way merge across sources. The
  Postgres backend pushes filters, keyset pagination, and the stats `GROUP BY`
  down to the database, so it scales to production log tables.
- Keyset cursor pagination across all sources (`next_cursor`).
- Configurable field mapping into a normalized log schema.
- Redaction before any log content is returned. Configured sensitive fields
  *add to* the built-in defaults instead of replacing them. Free-text search is
  matched after redaction, so redacted secrets are never searchable. Stats
  labels and warnings are redacted and bounded too, and nested redaction is
  depth-limited.
- Default time windows, hard result limits, and a bounded scan guard.
- Four read-only tools: `search_logs`, `get_log_detail`,
  `get_correlation_logs`, and `get_log_stats`.
- MCP prompt templates and agent instruction files for safe debugging workflows.

## Why Not Just Generate One?

An agent can scaffold a log-reading MCP in minutes, and for one small,
non-sensitive file that is fine. The cost shows up in the parts a quick build
tends to skip:

- **Secrets stay out of context.** Redaction runs by field and pattern before
  any content leaves the process — labels and warnings included — and free-text
  search matches *after* redaction. A generated server usually returns log lines
  verbatim, secrets and all.
- **Read-only by construction, not by trust.** `mode=ro` + `PRAGMA query_only`
  on SQLite, a read-only path on Postgres. The agent cannot alter or drop your
  logs.
- **Bounded.** Default time windows, hard result caps, and a scan guard stop one
  query from pulling a whole table into memory or context.
- **Lossless pagination across sources.** The `(timestamp, id, source)` keyset
  and k-way merge keep rows from being dropped or duplicated at page
  boundaries — the failure mode of naive offset paging, and an invisible one.
- **One contract for every backend.** JSONL, SQLite, and Postgres behind the
  same four tools and one config — configured once, not rebuilt and re-debugged
  per repository.
- **Tested.** Redaction, pagination, SQL generation, and multi-source merge are
  covered, not assumed.

The easy 80% is reading a row; Cairn is the 20% — secrets, scale, and
correctness — that a one-off rarely gets right.

## Install For Development

```bash
uv sync --extra dev
uv run pytest
```

Run the JSONL example:

```bash
uv run cairn-mcp --config examples/jsonl/config.yaml
```

Create and run the SQLite example:

```bash
uv run python examples/sqlite/create_sample_db.py
uv run cairn-mcp --config examples/sqlite/config.yaml
```

Run against Postgres (requires the optional driver):

```bash
uv sync --extra postgres
export LOG_DATABASE_URL='postgresql://reader:secret@localhost:5432/app'
uv run cairn-mcp --config examples/postgres/config.yaml
```

Run the optional Postgres integration smoke test against a disposable database:

```bash
uv sync --extra dev --extra postgres
export CAIRN_POSTGRES_TEST_DSN='postgresql://writer:secret@localhost:5432/test_logs'
uv run --extra dev --extra postgres pytest -m integration tests/test_postgres_integration.py
```

The integration test creates and drops its own temporary schema. Use a test
database user that is allowed to create schemas.

After publishing, the server is intended to run through `uvx`:

```bash
uvx cairn-mcp --config /absolute/path/to/config.yaml
```

You can also set the config path with:

```bash
export CAIRN_CONFIG=/absolute/path/to/config.yaml
cairn-mcp
```

## MCP Client Example

```json
{
  "mcpServers": {
    "structured-logs": {
      "command": "uvx",
      "args": [
        "cairn-mcp",
        "--config",
        "/absolute/path/to/config.yaml"
      ]
    }
  }
}
```

## Configuration

```yaml
default_time_window: 24h
max_results: 100

redaction:
  redact_emails: false
  # These ADD to the built-in sensitive fields (password, token, api_key,
  # authorization, …); they do not replace them.
  fields:
    - session_id
  patterns:
    - name: session_id
      pattern: "session_[A-Za-z0-9]+"

sources:
  app_jsonl:
    type: jsonl
    path: ./logs.jsonl
    field_map:
      timestamp: ts
      level: severity
      message: msg
      logger: logger_name
      request_id: req_id
      trace_id: trace

  app_sqlite:
    type: sqlite
    path: ./app_logs.db
    table: logs
    field_map:
      timestamp: created_at
      level: severity
      message: body
      request_id: request_id

  app_postgres:
    type: postgres
    dsn: ${LOG_DATABASE_URL}
    table: log_entries
    field_map:
      id: id
      timestamp: timestamp
      level: level
      message: message
      attributes: extra
```

Config paths are resolved relative to the YAML file. Source `path` and `dsn`
values support environment expansion such as `${PROJECT_ROOT}/logs/app.jsonl`
(expansion is limited to those fields so a process secret can't be pulled into,
say, a `type` value that an error later echoes).

## Tool Behavior

- `search_logs`: compact redacted entries (newest first), no full traceback or
  attributes. Returns `next_cursor`; pass it back as `cursor` to page on.
- `get_log_detail`: one entry by ID, including redacted traceback and attributes.
- `get_correlation_logs`: ordered sequence (oldest first) for `request_id`,
  `trace_id`, or `job_id`, with `next_cursor` paging.
- `get_log_stats`: counts by level, service, logger, and time bucket.

All tools are read-only and the server never exposes raw SQL. Push-down per
backend:

| Backend  | Filters | Ordering / pagination | Stats | Notes |
|----------|---------|-----------------------|-------|-------|
| postgres | full (SQL `WHERE`) | full (keyset, indexed) | `GROUP BY` | scales to large tables |
| sqlite   | equality filters in SQL | in-process, bounded by `scan_limit` | streamed | timestamp format-agnostic |
| jsonl    | in-process | in-process | streamed | dev-scale convenience |

Postgres connections are pooled (reused across queries, with a liveness check on
borrow) and opened read-only. SQLite connections use `mode=ro` and
`PRAGMA query_only`. Table and column names are validated as identifiers and all
values are bound as parameters.

## Extending With Custom Backends

Backends register themselves under a `type` name. A third-party package adds a
new source type without touching this repo by exposing an entry point:

```toml
[project.entry-points."cairn_mcp.backends"]
loki = "my_package.loki_backend:LokiBackend"
```

A backend subclasses `LogBackend` and implements `fetch(LogQuery)` and
`get_by_id(...)` (and optionally `aggregate(...)` for native stats). The adapter
discovers it at startup; `type: loki` in a source then just works.

If you do not override `aggregate(...)`, the base implementation pages through
`fetch(...)` and is bounded by `LogBackend.aggregate_scan_limit` (and stops if a
backend fails to advance the cursor). Backends over large sources should override
`aggregate(...)` with native grouping rather than rely on the paging fallback.

## Normalized Log Schema

Required fields are `timestamp`, `level`, and `message`. Optional fields are
`id`, `logger`, `service`, `environment`, `request_id`, `trace_id`, `job_id`,
`user_id`, `exception_type`, `traceback`, and `attributes`.

If a source does not provide an ID, JSONL entries use `source:line_number` and
SQLite entries use `source:rowid`.

## Agent Safety

Log text is untrusted evidence, not instruction. The included agent files teach
clients to query logs first when runtime IDs or time windows are available, to
fetch correlated sequences, and to ignore any commands or instructions embedded
inside log messages.

See:

- [Agent integration](docs/agent-integration.md)
- [Prompt injection safety](docs/prompt-injection-safety.md)
- [Configuration](docs/configuration.md)

## Out Of Scope

This MVP intentionally excludes a web UI, OTLP ingestion, hosted deployment,
metrics/traces as first-class signals, vendor observability connectors,
natural-language-to-SQL, writes, alerting, auth, and production remote MCP
deployment.
