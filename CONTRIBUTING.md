# Contributing to Cairn

Thanks for taking the time to contribute. Cairn is small on purpose, so a little
shared context keeps it that way.

## Scope — read this first

Cairn is a **read-only adapter**, not a log platform. The following are
deliberately **out of scope**, and PRs adding them will be declined:

- ingestion pipelines or log shipping,
- an observability UI or dashboards,
- a raw-SQL (or arbitrary-query) tool,
- any write, update, or delete operation against a source.

Work that makes the adapter safer, faster, or able to read more log shapes is
very welcome.

## Invariants that must always hold

Every change must preserve — and, where relevant, test — these:

1. **Read-only by construction.** No code path may mutate a source
   (`mode=ro` + `PRAGMA query_only` for SQLite, a read-only path for Postgres).
2. **Redaction before egress.** No log content leaves the process unredacted.
   Free-text search must match *after* redaction, so secrets are never
   searchable.
3. **Bounded.** Default time windows, hard result caps, and the scan guard stay
   intact; no query may pull an unbounded amount into memory or context.

## Development

```bash
uv sync --extra dev --extra postgres
uv run pytest
```

Tests live in `tests/`. New behavior needs tests; bug fixes should come with a
regression test. The `integration` marker is for tests that need external
services.

## Adding a backend

Backends implement `LogBackend` and register via the `cairn_mcp.backends` entry
point group (see the example in `pyproject.toml`). A backend must honor the
push-down query contract (filters, ordering, limits), apply the normalized log
schema, and respect redaction and the bound/scan guards. Add tests covering
query generation, pagination at page boundaries, and redaction.

## Pull requests

- Keep PRs small and focused.
- Update `CHANGELOG.md` under `[Unreleased]` for any user-facing change.
- Update `docs/` and `README.md` when behavior or configuration changes.
- CI (`pytest` on Python 3.11–3.13) must pass.

## Security

Please do **not** open a public issue for a vulnerability. Report it privately —
see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
