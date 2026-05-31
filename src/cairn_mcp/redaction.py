from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from cairn_mcp.config import RedactionConfig
from cairn_mcp.schema import LogEntry


DEFAULT_REDACTION = "[REDACTED]"
DEFAULT_MAX_STRING_LENGTH = 8_000
# Bound recursive redaction so a deeply nested or enormous attributes object
# can't exhaust the stack (RecursionError) or run unbounded work.
DEFAULT_MAX_DEPTH = 48
DEFAULT_MAX_NODES = 50_000

# Single-word markers matched against whole tokens of a field name. "token"
# matches "refresh_token" / "authToken" but NOT "tokenize" or "tokens_emitted".
_SINGLE_WORD_MARKERS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "authorization",
        "auth",
        "credential",
        "credentials",
    }
)
# Compound markers matched against the joined alphanumeric form of a field name,
# so "api_key", "apiKey" and "api-key" all collapse to "apikey".
_COMPOUND_MARKERS = (
    "apikey",
    "apisecret",
    "accesskey",
    "secretkey",
    "privatekey",
    "accesstoken",
    "refreshtoken",
    "sessiontoken",
    "bearertoken",
    "clientsecret",
)

# Compound secret-key names for the unstructured-string scanner. Compounds must
# precede their substrings so "access_token" matches fully (and a plain
# "\btoken" can't match it anyway — the underscore is not a word boundary).
_SECRET_KEY = (
    r"access[_-]?token|refresh[_-]?token|session[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|api[_-]?secret|secret[_-]?key|private[_-]?key|"
    r"auth[_-]?token|api[_-]?key|apikey|password|passwd|pwd|secret|token"
)

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _field_tokens(field_name: str) -> tuple[set[str], str]:
    """Split a field name into lowercase tokens plus its joined alnum form."""
    spaced = _CAMEL_BOUNDARY.sub(" ", field_name).lower()
    parts = [part for part in _NON_ALNUM.split(spaced) if part]
    return set(parts), "".join(parts)


class Redactor:
    def __init__(
        self,
        config: RedactionConfig | None = None,
        *,
        max_string_length: int = DEFAULT_MAX_STRING_LENGTH,
        max_depth: int = DEFAULT_MAX_DEPTH,
        max_nodes: int = DEFAULT_MAX_NODES,
    ) -> None:
        self.config = config or RedactionConfig()
        self.max_string_length = max_string_length
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.sensitive_fields = self.config.effective_fields()
        self.patterns: list[tuple[re.Pattern[str], str]] = [
            (
                re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
                f"Bearer {DEFAULT_REDACTION}",
            ),
            # Authorization header, any scheme (Bearer/Basic/Digest/Token/…). Runs
            # before the generic key=value rule so "Authorization: Basic <b64>"
            # masks the whole credential, not just the scheme word.
            (
                re.compile(
                    r"\bauthorization\s*[:=]\s*\S+(?:\s+[A-Za-z0-9._~+/=-]+)?",
                    re.IGNORECASE,
                ),
                f"Authorization: {DEFAULT_REDACTION}",
            ),
            (
                re.compile(
                    rf"\b({_SECRET_KEY})\s*[:=]\s*['\"]?[^'\"\s,;}}]+",
                    re.IGNORECASE,
                ),
                rf"\1={DEFAULT_REDACTION}",
            ),
        ]
        if self.config.redact_emails:
            self.patterns.append(
                (
                    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
                    DEFAULT_REDACTION,
                )
            )
        for pattern_config in self.config.patterns:
            self.patterns.append(
                (re.compile(pattern_config.pattern), pattern_config.replacement)
            )

    def redact_entry(
        self,
        entry: LogEntry,
        *,
        include_traceback: bool = True,
        include_attributes: bool = True,
    ) -> dict[str, Any]:
        data = entry.model_dump(mode="json", exclude_none=True)
        if not include_traceback:
            data.pop("traceback", None)
        if not include_attributes:
            data.pop("attributes", None)
        return self.redact_value(data)

    def redact_value(self, value: Any, *, field_name: str | None = None) -> Any:
        return self._redact(value, field_name, 0, [0])

    def redact_text(self, value: str) -> str:
        """Redact a free-standing string with the same rules as log content.

        Used for values that bypass entry redaction — stats bucket labels and
        warning strings — so they can't leak what entry redaction would mask.
        """
        return self._redact_string(value)

    def _redact(self, value: Any, field_name: str | None, depth: int, budget: list[int]) -> Any:
        budget[0] += 1
        if depth > self.max_depth or budget[0] > self.max_nodes:
            return "[TRUNCATED]"
        if field_name and self._is_sensitive_field(field_name):
            return DEFAULT_REDACTION
        if isinstance(value, str):
            return self._redact_string(value)
        if isinstance(value, Mapping):
            return {
                str(key): self._redact(item, str(key), depth + 1, budget)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item, None, depth + 1, budget) for item in value]
        if isinstance(value, tuple):
            return [self._redact(item, None, depth + 1, budget) for item in value]
        return value

    def _is_sensitive_field(self, field_name: str) -> bool:
        lowered = field_name.lower()
        if lowered in self.sensitive_fields:
            return True
        tokens, joined = _field_tokens(field_name)
        if tokens & _SINGLE_WORD_MARKERS:
            return True
        return any(marker in joined for marker in _COMPOUND_MARKERS)

    def _redact_string(self, value: str) -> str:
        # Truncate BEFORE running patterns so a configured catastrophic-backtracking
        # regex can only ever see a bounded input (caps ReDoS amplification by size).
        redacted = value
        if len(redacted) > self.max_string_length:
            omitted = len(redacted) - self.max_string_length
            redacted = f"{redacted[: self.max_string_length]}... [truncated {omitted} chars]"
        for pattern, replacement in self.patterns:
            redacted = pattern.sub(replacement, redacted)
        return redacted
