# Security Policy

Cairn is a **read-only** log adapter whose core promise is that secrets in the
logs you expose are redacted before any content leaves the process. Reports
about that boundary — or anything else — are very welcome.

## Reporting a Vulnerability

Please report vulnerabilities privately via GitHub's
[private vulnerability reporting](https://github.com/Appwood/cairn-mcp/security/advisories/new)
(the repository **Security** tab → **Report a vulnerability**). We aim to
acknowledge reports within a few business days.

Please do **not** open a public issue for a security problem before a fix is
available.

When reporting, please include:

- the affected version (`cairn-mcp --version`) and backend (jsonl / sqlite / postgres),
- a minimal config and log shape that reproduces the issue,
- for redaction bypasses: the input and the unredacted output you observed.

## Scope

In scope: redaction bypasses, read-only escapes (any write reaching a backend),
SQL or identifier injection, path traversal beyond a configured source, and
unbounded resource use a single query can trigger.

Out of scope: the contents of your own logs, and the documented fact that
**configuration is trusted** — an operator who controls the config file can
already point a source at any path or DSN they are able to read.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |

As a pre-1.0 project, fixes land on the latest minor release.
