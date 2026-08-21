"""Private redaction helpers shared by the public error contract.

This module is intentionally private; only scalar-safe values are ever
rendered into public or telemetry records.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED_VALUE = "<redacted>"

_MAX_SCALAR_LENGTH = 512

_SENSITIVE_KEY_TOKENS = (
    "secret",
    "password",
    "passwd",
    "credential",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
)

#: URLs embedding ``user:password@`` credentials are always redacted.
_URL_WITH_CREDENTIALS = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/@\s]+:[^/@\s]+@")

_FORBIDDEN_VALUE_PATTERNS = (
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "password=",
    "passwd=",
    "api_key=",
    "apikey=",
    "Bearer ",
    "Basic ",
    "secret=",
)


def _is_forbidden_value(text: str) -> bool:
    lowered = text.lower()
    return any(pattern.lower() in lowered for pattern in _FORBIDDEN_VALUE_PATTERNS)


def redact_scalar(value: Any) -> str:
    """Render an arbitrary value as a safe scalar string.

    Secrets, credentials, binary data and non-scalar objects are replaced
    with a redaction marker; long strings are truncated.
    """
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        if _is_forbidden_value(value) or _URL_WITH_CREDENTIALS.match(value):
            return REDACTED_VALUE
        return value if len(value) <= _MAX_SCALAR_LENGTH else value[:_MAX_SCALAR_LENGTH] + "..."
    if isinstance(value, (bytes, bytearray, memoryview)):
        return REDACTED_VALUE
    return REDACTED_VALUE


def redact_key_value(key: Any, value: Any) -> str:
    """Redact a key/value pair, treating sensitive key names as secret-bearing."""
    lowered = str(key).lower()
    if any(token in lowered for token in _SENSITIVE_KEY_TOKENS):
        return REDACTED_VALUE
    return redact_scalar(value)
