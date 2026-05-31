# Configuration

The server loads one YAML file from `--config` or
`CAIRN_CONFIG`.

## Top-Level Fields

- `sources`: required map of named log sources.
- `default_time_window`: optional duration such as `15m`, `2h`, or `7d`.
- `max_results`: optional page size cap. Defaults to `100` and cannot exceed `500`.
- `max_scan`: optional upper bound on rows examined per call when free-text
  search or text-filtered stats force a scan. Defaults to `5000`.
- `redaction`: optional redaction policy.

## Sources

JSONL source:

```yaml
sources:
  app:
    type: jsonl
    path: ./logs.jsonl
    field_map:
      timestamp: ts
      level: severity
      message: msg
```

SQLite source:

```yaml
sources:
  app_db:
    type: sqlite
    path: ./app_logs.db
    table: logs
    scan_limit: 50000   # optional per-source cap on rows pulled before filtering
    field_map:
      timestamp: created_at
      level: severity
      message: body
```

Postgres source (needs the optional `psycopg` driver — install with the
`postgres` extra). A unique, orderable `id` column is required for keyset
pagination:

```yaml
sources:
  app_pg:
    type: postgres
    dsn: ${LOG_DATABASE_URL}
    table: log_entries
    options:
      schema: public      # optional, defaults to the search path
    field_map:
      id: id
      timestamp: timestamp
      level: level
      message: message
      attributes: extra   # a JSONB column maps cleanly to attributes
```

`field_map` maps normalized field names to source keys or column names.
Required normalized fields are `timestamp`, `level`, and `message`. The
`type` value is resolved against the backend registry, so custom backends
registered via entry points can be referenced here too.

## Redaction

Redaction runs after normalization and before any MCP response.

```yaml
redaction:
  redact_emails: true
  fields:
    - session_id            # ADDED to the built-in sensitive fields
  patterns:
    - name: internal_session
      pattern: "session_[A-Za-z0-9]+"
      replacement: "[SESSION]"
```

Bearer tokens, API-key-like strings, and obvious password or secret fields are
masked by default. Field names are matched token-aware: `refresh_token`,
`apiKey`, and `X-Authorization` are redacted, while lookalikes such as
`tokenize_strategy` or `secretariat_email` are not.

`fields` **adds to** the built-in sensitive-field set; configuring it can never
silently weaken the defaults. To fully control the list, set
`replace_default_fields: true` (the token-aware heuristic still applies). Invalid
`patterns` are rejected when the config loads, not at first query.

Custom `patterns` are run with `re.sub` against untrusted log text. Inputs are
truncated to a bounded length before matching, but a catastrophic-backtracking
regex (e.g. `(a+)+$`) can still stall response generation — avoid nested
unbounded quantifiers in custom patterns.

## Environment Expansion

`${VAR}` references are expanded in source `path` and `dsn` values only (e.g.
`path: ${PROJECT_ROOT}/logs/app.jsonl`, `dsn: ${LOG_DATABASE_URL}`). Other fields
are taken literally, so a process secret can't be pulled into a value such as
`type` that an error message might echo.
