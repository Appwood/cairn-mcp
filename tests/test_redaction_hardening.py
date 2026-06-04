"""Regression tests for confirmed redaction bypasses.

Each test below corresponds to a secret value that reached the caller
unredacted in 0.1.0, on extremely common log shapes (quoted multi-word
secrets, URL-embedded credentials, Set-Cookie/session, and secret-bearing
values under benign-looking field names). The headline promise is
"secrets never escape", so these are treated as security regressions.
"""

from __future__ import annotations

from cairn_mcp.config import RedactionConfig
from cairn_mcp.redaction import DEFAULT_REDACTION, Redactor


def test_quoted_multiword_secret_value_is_fully_masked() -> None:
    # The value class must consume a quoted value INCLUDING spaces, not stop at
    # the first whitespace and leak the tail.
    out = Redactor().redact_text('api_key="SECRET WITH SPACE" tail')

    assert "SECRET" not in out
    assert "WITH SPACE" not in out
    assert "tail" in out  # text after the quoted value stays visible


def test_url_embedded_credentials_are_masked() -> None:
    redactor = Redactor()
    cases = {
        "postgres://app:S3cretPw@db:5432/main": "S3cretPw",
        "mysql://root:hunter2@host/db": "hunter2",
        "redis://:onlypass@cache:6379/0": "onlypass",  # password-only userinfo
        "amqp://user:p%40ss-word@broker": "p%40ss-word",
    }
    for dsn, secret in cases.items():
        out = redactor.redact_text(f"connect failed: {dsn}")
        assert secret not in out, f"{secret!r} leaked from {dsn!r}: {out}"
        assert "://" in out  # the scheme/host shape is preserved, only creds masked


def test_set_cookie_and_session_assignment_is_masked() -> None:
    redactor = Redactor()

    assert "SUPERSECRETVAL" not in redactor.redact_text(
        "Set-Cookie: session=SUPERSECRETVAL; HttpOnly"
    )
    assert "TOPSECRET" not in redactor.redact_text("using credentials=TOPSECRET now")
    assert "PHRASE123" not in redactor.redact_text("passphrase=PHRASE123")


def test_bare_jwt_is_masked_anywhere() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.UNIQUESIGNATURE123"
    out = Redactor().redact_text(f"auth header was {jwt} end")

    assert "UNIQUESIGNATURE123" not in out
    assert "eyJ" not in out
    assert "end" in out


def test_secret_values_under_benign_field_names_do_not_leak() -> None:
    # The exact repro from the review: secrets carried as VALUES under
    # innocuous-looking keys. Closed via value-shape patterns, not field names.
    data = Redactor().redact_value(
        {
            "cookie": "session=SUPERSECRETVAL",
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.UNIQUESIGNATURE123",
            "connection_string": "mysql://root:hunter2@h/db",
        }
    )
    blob = str(data)

    assert "SUPERSECRETVAL" not in blob
    assert "UNIQUESIGNATURE123" not in blob
    assert "hunter2" not in blob


def test_passphrase_field_is_redacted() -> None:
    redactor = Redactor()

    assert redactor._is_sensitive_field("passphrase") is True
    data = redactor.redact_value({"passphrase": "correct horse battery staple"})
    assert data["passphrase"] == DEFAULT_REDACTION


def test_benign_session_fields_stay_visible() -> None:
    # We deliberately do NOT add bare 'session'/'cookie' to the hardcoded field
    # markers, to avoid over-redacting common operational fields. Secret session
    # VALUES are still caught by the inline/value-shape patterns above.
    redactor = Redactor()

    assert redactor._is_sensitive_field("session_count") is False
    assert redactor._is_sensitive_field("session_duration_ms") is False


def test_plain_urls_without_credentials_are_untouched() -> None:
    # Guard against over-redaction: a normal URL (no user:pass@) must survive.
    redactor = Redactor()
    for url in (
        "https://example.com:8080/path?q=1",
        "GET http://api.internal/v1/users",
        "ssh://gituser@host/repo.git",  # user, but no password
    ):
        assert redactor.redact_text(url) == url


def test_ported_url_with_at_in_query_is_not_mangled() -> None:
    # Regression: the URL-credential pattern must not run from the port colon
    # across the path/query to a later '@' (e.g. an email in a query string),
    # which corrupted a normal URL into scheme://host:[REDACTED]@...
    redactor = Redactor()
    for url in (
        "https://example.com:8080/path?email=a@b.com",
        "https://api.example.com:443/users?contact=admin@example.org",
        "http://host:9000/cb?next=u@v.com&z=1",
    ):
        assert redactor.redact_text(url) == url, url
