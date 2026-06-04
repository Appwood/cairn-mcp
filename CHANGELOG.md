# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-06-04

### Added

- Initial public release.
- Local stdio MCP server (official Python MCP SDK) exposing four read-only
  tools — `search_logs`, `get_log_detail`, `get_correlation_logs`, and
  `get_log_stats` — plus debugging prompt templates.
- JSONL, SQLite, and Postgres backends behind one push-down query contract,
  pluggable via `cairn_mcp.backends` entry points.
- Keyset cursor pagination with a k-way merge across sources.
- Field- and pattern-based redaction applied before any log content is
  returned — Authorization headers, `key=value` secrets (including quoted
  multi-word values), URL-embedded credentials, and bare JWTs included — with
  free-text search matched after redaction, so secrets are never searchable.
- Configurable field mapping into a normalized log schema, default time
  windows, hard result caps, and a bounded scan guard.

[Unreleased]: https://github.com/Appwood/cairn-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Appwood/cairn-mcp/releases/tag/v0.1.0
