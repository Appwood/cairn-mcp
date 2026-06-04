<!-- Security issue? Do not open a public PR/issue — see SECURITY.md. -->

## What and why

<!-- What does this change, and why? Link any related issue. -->

## Checklist

- [ ] Tests added/updated and `uv run pytest` passes
- [ ] Preserves Cairn's invariants: **read-only**, **redaction before any content leaves the process**, and **bounded** queries (time window / result cap / scan guard)
- [ ] Stays within scope — adapter, not a log platform (no ingestion, UI, raw SQL, or write operations)
- [ ] Updated `CHANGELOG.md` under `[Unreleased]` (if user-facing)
- [ ] Updated docs (`docs/`, `README.md`) if behavior or config changed
