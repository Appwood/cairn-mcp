from __future__ import annotations

from cairn_mcp.config import RedactionConfig
from cairn_mcp.redaction import DEFAULT_REDACTION, Redactor
from cairn_mcp.schema import LogEntry


def test_redaction_masks_common_patterns_and_sensitive_fields() -> None:
    entry = LogEntry(
        id="log-1",
        timestamp="2026-05-30T07:00:00Z",
        level="ERROR",
        message="failed with bearer abc.def and api_key=secret-value for a@example.com",
        traceback="RuntimeError: token=abc123",
        attributes={
            "password": "open-sesame",
            "nested": {"refresh_token": "token-value", "safe": "visible"},
        },
    )
    redactor = Redactor(RedactionConfig(redact_emails=True))

    data = redactor.redact_entry(entry)

    assert "abc.def" not in data["message"]
    assert "secret-value" not in data["message"]
    assert "a@example.com" not in data["message"]
    assert data["attributes"]["password"] == DEFAULT_REDACTION
    assert data["attributes"]["nested"]["refresh_token"] == DEFAULT_REDACTION
    assert data["attributes"]["nested"]["safe"] == "visible"


def test_compact_redaction_can_exclude_traceback_and_attributes() -> None:
    entry = LogEntry(
        id="log-1",
        timestamp="2026-05-30T07:00:00Z",
        level="ERROR",
        message="boom",
        traceback="large traceback",
        attributes={"secret": "value"},
    )

    data = Redactor().redact_entry(
        entry, include_traceback=False, include_attributes=False
    )

    assert "traceback" not in data
    assert "attributes" not in data


def test_configured_fields_merge_with_defaults_instead_of_replacing() -> None:
    # User adds 'custom_field' but must NOT lose the built-in 'password' default.
    redactor = Redactor(RedactionConfig(fields=["custom_field"]))

    assert redactor._is_sensitive_field("custom_field") is True
    assert redactor._is_sensitive_field("password") is True
    assert redactor._is_sensitive_field("authorization") is True


def test_replace_default_fields_opt_out() -> None:
    redactor = Redactor(
        RedactionConfig(fields=["only_this"], replace_default_fields=True)
    )

    assert redactor._is_sensitive_field("only_this") is True
    # explicit opt-out drops the defaults' exact membership...
    assert "password" not in redactor.sensitive_fields
    # ...but the token heuristic still catches obvious secrets
    assert redactor._is_sensitive_field("password") is True


def test_field_detection_has_no_false_positives() -> None:
    redactor = Redactor()

    # these merely contain a marker as a substring and must stay visible
    assert redactor._is_sensitive_field("tokenize_strategy") is False
    assert redactor._is_sensitive_field("secretariat_email") is False
    assert redactor._is_sensitive_field("authored_by") is False
    assert redactor._is_sensitive_field("keyboard_layout") is False


def test_field_detection_catches_compounds_and_camelcase() -> None:
    redactor = Redactor()

    assert redactor._is_sensitive_field("api_key") is True
    assert redactor._is_sensitive_field("apiKey") is True
    assert redactor._is_sensitive_field("refresh_token") is True
    assert redactor._is_sensitive_field("authToken") is True
    assert redactor._is_sensitive_field("client_secret") is True
    assert redactor._is_sensitive_field("X-Authorization") is True


def test_default_patterns_cover_compound_tokens_and_authorization() -> None:
    redactor = Redactor()
    message = (
        "access_token=AAA refresh_token=BBB client_secret=CCC api_key=DDD "
        "session_token=EEE Authorization: Basic dXNlcjpwYXNz"
    )

    out = redactor.redact_text(message)

    for secret in ("AAA", "BBB", "CCC", "DDD", "EEE", "dXNlcjpwYXNz"):
        assert secret not in out, f"{secret} leaked: {out}"


def test_recursive_redaction_is_depth_bounded() -> None:
    import json

    root: dict = {}
    inner = root
    for _ in range(2000):
        child: dict = {}
        inner["next"] = child
        inner = child
    inner["leaf"] = "value"

    # must not raise RecursionError, and must stop at the depth cap
    out = Redactor(max_depth=48).redact_value(root)
    assert "[TRUNCATED]" in json.dumps(out)


def test_recursive_redaction_is_node_bounded() -> None:
    wide = {"attrs": [{"k": i} for i in range(100)]}
    out = Redactor(max_nodes=10).redact_value(wide)
    assert "[TRUNCATED]" in str(out)


def test_long_strings_are_truncated_before_pattern_matching() -> None:
    redactor = Redactor(max_string_length=50)
    out = redactor.redact_text("token=SECRET " + "a" * 200)

    assert "[truncated" in out          # bounded before the regex engine sees it
    assert "SECRET" not in out          # secrets within the cap are still masked
    assert len(out) < 120
